"""Authenticated GET-only transport for Bybit V5 HMAC API keys."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from http.client import HTTPException, HTTPResponse, HTTPSConnection
from typing import cast
from urllib.parse import urlencode, urlsplit

from freqtrade_truth.adapters.http import HttpTransportError, JsonObject, JsonValue

_DEFAULT_RECV_WINDOW_MS = 5000
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class BybitV5HmacTransport:
    """GET-only Bybit V5 transport using system-generated HMAC API keys.

    The exact query string used in the request is also used in the signature:
    timestamp + api_key + recv_window + query_string.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str,
        api_secret: str,
        recv_window_ms: int = _DEFAULT_RECV_WINDOW_MS,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        parsed = urlsplit(base_url)

        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials must not be embedded in base_url")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain query parameters or fragments")
        if parsed.hostname is None:
            raise ValueError("base_url must contain a hostname")
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not api_secret:
            raise ValueError("api_secret must not be empty")
        if recv_window_ms <= 0:
            raise ValueError("recv_window_ms must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")

        self._hostname = parsed.hostname
        self._port = parsed.port
        self._base_path = parsed.path.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._recv_window_ms = recv_window_ms
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._clock_ms = clock_ms or _system_clock_ms

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
        clean_path = _validate_api_path(path)
        query_string = _encode_query(params)
        timestamp = self._clock_ms()

        if timestamp < 0:
            raise ValueError("clock_ms must return a non-negative millisecond timestamp")

        timestamp_text = str(timestamp)
        recv_window_text = str(self._recv_window_ms)
        signature_payload = (
            f"{timestamp_text}{self._api_key}{recv_window_text}{query_string}"
        )
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            signature_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        target = f"{self._base_path}/{clean_path}"
        if not target.startswith("/"):
            target = f"/{target}"
        if query_string:
            target = f"{target}?{query_string}"

        headers = {
            "Accept": "application/json",
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": timestamp_text,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": recv_window_text,
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
                    f"HTTP request failed with status {response.status}",
                    status_code=response.status,
                )

            raw_body = response.read(self._max_response_bytes + 1)
        except HttpTransportError:
            raise
        except (HTTPException, TimeoutError, OSError) as exc:
            raise HttpTransportError("HTTP request failed") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()

        if len(raw_body) > self._max_response_bytes:
            raise HttpTransportError("HTTP response exceeded configured size limit")

        try:
            decoded_body = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HttpTransportError("HTTP response was not valid UTF-8") from exc

        try:
            payload = json.loads(decoded_body, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise HttpTransportError("HTTP response was not valid JSON") from exc

        if not isinstance(payload, dict):
            raise HttpTransportError("HTTP response must be a JSON object")

        return cast(JsonObject, payload)


def _system_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _validate_api_path(path: str) -> str:
    clean_path = path.lstrip("/")
    if not clean_path or "?" in clean_path or "#" in clean_path:
        raise ValueError("path must be a non-empty API path without query or fragment")
    if any(segment == ".." for segment in clean_path.split("/")):
        raise ValueError("path traversal is not allowed")
    if not clean_path.startswith("v5/"):
        raise ValueError("Bybit authenticated transport only permits v5 API paths")
    return clean_path


def _encode_query(
    params: Mapping[str, str | int | bool] | None,
) -> str:
    if not params:
        return ""

    normalized: list[tuple[str, str | int]] = []
    for key, value in params.items():
        if not key or any(char in key for char in "\r\n"):
            raise ValueError("query parameter names must be non-empty and single-line")
        if isinstance(value, bool):
            normalized.append((key, "true" if value else "false"))
        else:
            normalized.append((key, value))

    return urlencode(normalized)
