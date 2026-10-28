"""Minimal authenticated OKX V5 transport with a single GET-only allowlisted path.

Use only keys separately provisioned with the OKX Read permission. No key
permission introspection is claimed; this transport cannot send POST.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from http.client import HTTPException, HTTPResponse, HTTPSConnection
from typing import cast
from urllib.parse import urlencode, urlsplit

from freqtrade_truth.adapters.http import HttpTransportError, JsonObject

_ALLOWED_HOSTS = frozenset({"www.okx.com", "eea.okx.com", "us.okx.com"})
_ALLOWED_PATH = "api/v5/account/positions-history"
_MAX_BYTES = 10 * 1024 * 1024


class OkxV5ReadOnlyHmacTransport:
    """Read-only GET transport; accepts only OKX positions-history REST path.

    The exact timestamp + 'GET' + request path and query is signed using
    HMAC-SHA256 and base64, according to the OKX V5 REST auth contract.
    """

    def __init__(
        self,
        base_url: str = "https://www.okx.com",
        *,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = _MAX_BYTES,
        clock: Callable[[], str] | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be the official supported OKX HTTPS API host")
        for name, value in (
            ("api_key", api_key),
            ("api_secret", api_secret),
            ("api_passphrase", api_passphrase),
        ):
            if not value or "\r" in value or "\n" in value:
                raise ValueError(f"{name} must be a nonempty single-line value")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("timeouts and response limits must be positive")
        self._hostname = parsed.hostname
        self._port = parsed.port
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._clock = clock or _timestamp_utc

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        return await asyncio.to_thread(self._get_json_sync, path, params)

    def _get_json_sync(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None,
    ) -> JsonObject:
        clean = path.lstrip("/")
        if clean != _ALLOWED_PATH or path != clean:
            raise ValueError("OKX authenticated GET path is not allowlisted")
        query = _query(params)
        target = f"/{clean}"
        if query:
            target = f"{target}?{query}"
        stamp = self._clock()
        if not stamp or "\r" in stamp or "\n" in stamp:
            raise ValueError("OKX timestamp must be single-line")
        payload_to_sign = f"{stamp}GET{target}".encode()
        signature = base64.b64encode(
            hmac.new(
                self._api_secret.encode("utf-8"),
                payload_to_sign,
                hashlib.sha256,
            ).digest()
        ).decode("ascii")
        headers = {
            "Accept": "application/json",
            "User-Agent": "freqtrade-truth/0.0.7",
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": stamp,
            "OK-ACCESS-PASSPHRASE": self._api_passphrase,
        }
        connection = HTTPSConnection(
            self._hostname,
            self._port,
            timeout=self._timeout_seconds,
        )
        response: HTTPResponse | None = None
        try:
            connection.request("GET", target, headers=headers)
            response = connection.getresponse()
            if response.status < 200 or response.status >= 300:
                raise HttpTransportError(
                    f"OKX HTTP status {response.status}",
                    status_code=response.status,
                )
            body = response.read(self._max_response_bytes + 1)
        except HttpTransportError:
            raise
        except (HTTPException, OSError, TimeoutError) as exc:
            raise HttpTransportError("OKX GET request failed") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()
        if len(body) > self._max_response_bytes:
            raise HttpTransportError("OKX response exceeded size limit")
        try:
            parsed = json.loads(body.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpTransportError("OKX response was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise HttpTransportError("OKX response must be a JSON object")
        return cast(JsonObject, parsed)


def _timestamp_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _query(params: Mapping[str, str | int | bool] | None) -> str:
    if not params:
        return ""
    allowed = frozenset({"instType", "instId", "after", "before", "limit", "posId", "type"})
    if any(name not in allowed for name in params):
        raise ValueError("OKX query parameter is not allowlisted")
    normalized: list[tuple[str, str | int]] = []
    for key, value in params.items():
        if isinstance(value, bool):
            normalized.append((key, "true" if value else "false"))
        else:
            normalized.append((key, value))
    return urlencode(normalized)
