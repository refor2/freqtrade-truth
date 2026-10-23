"""Minimal local-only read-only WSGI report API over an immutable report."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from freqtrade_truth.api.reporting import as_csv, as_html, as_json


def make_application(
    report: Mapping[str, object],
) -> Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]:
    """No external requests, persistence, mutable routes or order endpoints."""
    bodies = {
        "/": ("text/html; charset=utf-8", as_html(report).encode("utf-8")),
        "/report.json": ("application/json; charset=utf-8", as_json(report).encode("utf-8")),
        "/report.csv": ("text/csv; charset=utf-8", as_csv(report).encode("utf-8")),
        "/health": ("application/json; charset=utf-8", b'{"status":"ok","read_only":true}'),
    }

    def application(environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "")
        path = environ.get("PATH_INFO", "")
        if method != "GET":
            status, content_type, body = "405 Method Not Allowed", "text/plain", b"GET only"
        elif path not in bodies:
            status, content_type, body = "404 Not Found", "text/plain", b"Not found"
        else:
            status = "200 OK"
            content_type, body = bodies[path]
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'"),
        ]
        start_response(status, headers)
        return [body]

    return application
