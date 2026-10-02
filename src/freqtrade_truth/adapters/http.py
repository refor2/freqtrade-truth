"""Minimal read-only JSON transport used by public adapters."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from http.client import HTTPConnection, HTTPException, HTTPResponse, HTTPSConnection
from math import isfinite
from typing import Protocol, TypeAlias, cast, runtime_checkable
from urllib.parse import urlencode, urlsplit

JsonScalar: TypeAlias = str | int | Decimal | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

_DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_DEFAULT_RETRYABLE_STATUS_CODES = frozenset({429, 503})


class HttpTransportError(RuntimeError):
    """Raised when a read-only HTTP request cannot be completed safely."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


@runtime_checkable
class JsonHttpTransport(Protocol):
    """Small transport surface required by read-only adapters."""

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        """Perform one GET request and return a JSON object."""


class RetryingJsonTransport:
    """Bounded retry decorator for transient GET failures.

    Only explicitly configured HTTP status codes are retried. Retry-After is
    respected when present but capped so an upstream server cannot force an
    unbounded sleep.
    """

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.25,
        max_delay_seconds: float = 5.0,
        retryable_status_codes: frozenset[int] = _DEFAULT_RETRYABLE_STATUS_CODES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be non-negative")
        if max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be non-negative")
        if base_delay_seconds > max_delay_seconds:
            raise ValueError("base_delay_seconds must not exceed max_delay_seconds")
        if not retryable_status_codes:
            raise ValueError("retryable_status_codes must not be empty")
        if any(code < 100 or code > 599 for code in retryable_status_codes):
            raise ValueError("retryable_status_codes must contain valid HTTP status codes")

        self._transport = transport
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._retryable_status_codes = retryable_status_codes
        self._sleep = sleep

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        attempt = 1

        while True:
            try:
                return await self._transport.get_json(path, params)
            except HttpTransportError as exc:
                if (
                    attempt >= self._max_attempts
                    or exc.status_code not in self._retryable_status_codes
                ):
                    raise

                delay = self._retry_delay(exc, attempt)
                if delay > 0:
                    await self._sleep(delay)
                attempt += 1

    def _retry_delay(self, exc: HttpTransportError, attempt: int) -> float:
        if exc.retry_after_seconds is not None:
            delay = exc.retry_after_seconds
        else:
            delay = self._base_delay_seconds * (2 ** (attempt - 1))

        return min(max(delay, 0.0), self._max_delay_seconds)


class StdlibJsonTransport:
    """Dependency-free GET-only JSON transport with optional HTTP Basic Auth.

    The implementation uses the Python standard-library HTTP client so
    redirect behavior is explicit: 3xx responses are rejected and are never
    followed automatically.

    Secrets are retained in memory only and are never included in exception
    messages.
    """

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        parsed = urlsplit(base_url)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials must not be embedded in base_url")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain query parameters or fragments")
        if parsed.hostname is None:
            raise ValueError("base_url must contain a hostname")
        if (username is None) != (password is None):
            raise ValueError("username and password must be provided together")
        if username is not None and (not username or not password):
            raise ValueError("username and password must be non-empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if (
            username is not None
            and parsed.scheme == "http"
            and not _is_loopback_hostname(parsed.hostname)
        ):
            raise ValueError("authenticated non-loopback connections require HTTPS")

        self._scheme = parsed.scheme
        self._hostname = parsed.hostname
        self._port = parsed.port
        self._base_path = parsed.path.rstrip("/")
        self._username = username
        self._password = password
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

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
        target = self._request_target(path, params)
        headers = {
            "Accept": "application/json",
        }

        if self._username is not None and self._password is not None:
            raw_credentials = f"{self._username}:{self._password}".encode()
            token = base64.b64encode(raw_credentials).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        connection: HTTPConnection
        if self._scheme == "https":
            connection = HTTPSConnection(
                self._hostname,
                self._port,
                timeout=self._timeout_seconds,
            )
        else:
            connection = HTTPConnection(
                self._hostname,
                self._port,
                timeout=self._timeout_seconds,
            )

        response: HTTPResponse | None = None
        try:
            connection.request("GET", target, headers=headers)
            response = connection.getresponse()

            if response.status < 200 or response.status >= 300:
                retry_after = _parse_retry_after(response.getheader("Retry-After"))
                raise HttpTransportError(
                    f"HTTP request failed with status {response.status}",
                    status_code=response.status,
                    retry_after_seconds=retry_after,
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

    def _request_target(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None,
    ) -> str:
        clean_path = path.lstrip("/")

        if not clean_path or "?" in clean_path or "#" in clean_path:
            raise ValueError("path must be a non-empty API path without query or fragment")
        if any(segment == ".." for segment in clean_path.split("/")):
            raise ValueError("path traversal is not allowed")

        target = f"{self._base_path}/api/v1/{clean_path}"
        if not target.startswith("/"):
            target = f"/{target}"

        if params:
            target = f"{target}?{urlencode(params)}"

        return target


def _parse_retry_after(raw_value: str | None) -> float | None:
    if raw_value is None:
        return None

    value = raw_value.strip()
    if not value:
        return None

    try:
        seconds = float(value)
    except ValueError:
        seconds = -1.0

    if seconds >= 0 and isfinite(seconds):
        return seconds

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    return max((retry_at.astimezone(UTC) - now).total_seconds(), 0.0)


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False

    return address.is_loopback
