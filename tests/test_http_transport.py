import asyncio
import base64
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import ClassVar

import pytest

from freqtrade_truth.adapters.http import HttpTransportError, StdlibJsonTransport


class SyntheticHandler(BaseHTTPRequestHandler):
    seen_method: ClassVar[str | None] = None
    seen_path: ClassVar[str | None] = None
    seen_authorization: ClassVar[str | None] = None

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        type(self).seen_method = self.command
        type(self).seen_path = self.path
        type(self).seen_authorization = self.headers.get("Authorization")

        if self.path.startswith("/api/v1/redirect"):
            self.send_response(302)
            self.send_header("Location", "https://example.invalid")
            self.end_headers()
            return

        if self.path.startswith("/api/v1/rate-limit"):
            self.send_response(429)
            self.send_header("Retry-After", "2")
            self.end_headers()
            return

        if self.path.startswith("/api/v1/large"):
            body = b'{"value":"' + (b"x" * 256) + b'"}'
        else:
            body = b'{"value":1.25}'

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def synthetic_server() -> Iterator[str]:
    SyntheticHandler.seen_method = None
    SyntheticHandler.seen_path = None
    SyntheticHandler.seen_authorization = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "localhost:8080",
        "ftp://example.invalid",
        "https://example.invalid?debug=true",
        "https://example.invalid#fragment",
        "https://user:placeholder@example.invalid",
    ],
)
def test_transport_rejects_unsafe_or_ambiguous_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError):
        StdlibJsonTransport(base_url)


def test_transport_requires_complete_basic_auth_pair() -> None:
    with pytest.raises(ValueError, match="together"):
        StdlibJsonTransport("https://example.invalid", username="example")

    with pytest.raises(ValueError, match="together"):
        StdlibJsonTransport("https://example.invalid", password="<placeholder>")


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("", "<placeholder>"),
        ("example", ""),
    ],
)
def test_transport_rejects_empty_basic_auth_values(
    username: str,
    password: str,
) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        StdlibJsonTransport(
            "https://example.invalid",
            username=username,
            password=password,
        )


def test_transport_rejects_authenticated_plain_http_off_loopback() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        StdlibJsonTransport(
            "http://example.invalid",
            username="example",
            password="<placeholder>",
        )


def test_transport_requires_positive_limits() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        StdlibJsonTransport("https://example.invalid", timeout_seconds=0)

    with pytest.raises(ValueError, match="max_response_bytes"):
        StdlibJsonTransport("https://example.invalid", max_response_bytes=0)


@pytest.mark.parametrize("path", ["", "../trades", "trades?limit=1", "trades#fragment"])
def test_transport_rejects_ambiguous_paths(path: str) -> None:
    transport = StdlibJsonTransport("https://example.invalid")

    with pytest.raises(ValueError):
        asyncio.run(transport.get_json(path))


def test_transport_get_is_decimal_safe_and_preserves_query() -> None:
    with synthetic_server() as base_url:
        transport = StdlibJsonTransport(base_url)

        payload = asyncio.run(
            transport.get_json(
                "trades",
                {
                    "limit": 2,
                    "offset": 0,
                },
            )
        )

    assert payload["value"] == Decimal("1.25")
    assert SyntheticHandler.seen_method == "GET"
    assert SyntheticHandler.seen_path == "/api/v1/trades?limit=2&offset=0"
    assert SyntheticHandler.seen_authorization is None


def test_transport_basic_auth_is_allowed_on_loopback_only() -> None:
    with synthetic_server() as base_url:
        transport = StdlibJsonTransport(
            base_url,
            username="example",
            password="<placeholder>",
        )
        asyncio.run(transport.get_json("trades"))

    header = SyntheticHandler.seen_authorization
    assert header is not None
    assert header.startswith("Basic ")

    decoded = base64.b64decode(header.removeprefix("Basic ")).decode("utf-8")
    assert decoded == "example:<placeholder>"


def test_transport_does_not_follow_redirects() -> None:
    with synthetic_server() as base_url:
        transport = StdlibJsonTransport(base_url)

        with pytest.raises(HttpTransportError, match="302"):
            asyncio.run(transport.get_json("redirect"))


def test_transport_enforces_response_size_limit() -> None:
    with synthetic_server() as base_url:
        transport = StdlibJsonTransport(base_url, max_response_bytes=32)

        with pytest.raises(HttpTransportError, match="size limit"):
            asyncio.run(transport.get_json("large"))


def test_transport_exposes_retry_metadata_from_http_response() -> None:
    with synthetic_server() as base_url:
        transport = StdlibJsonTransport(base_url)

        with pytest.raises(HttpTransportError) as exc_info:
            asyncio.run(transport.get_json("rate-limit"))

    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after_seconds == 2.0
