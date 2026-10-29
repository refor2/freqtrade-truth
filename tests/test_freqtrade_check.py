"""No real bot, credentials, accounts or trading endpoints are used."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import urlsplit

import pytest

from freqtrade_truth.adapters.http import JsonObject
from freqtrade_truth.cli import main
from freqtrade_truth.freqtrade_cli import FreqtradeCheckConfig, run_freqtrade_check


def _trade() -> dict[str, object]:
    return {
        "trade_id": 101,
        "is_open": False,
        "pair": "BTC/USDT:USDT",
        "quote_currency": "USDT",
        "close_timestamp": int(datetime.now(UTC).timestamp() * 1000) - 1000,
        "profit_abs": "9876.54321",
        "funding_fees": "-0.12",
    }


class _FakeTransport:
    def __init__(self, *, endless: bool = False) -> None:
        self.requests: list[str] = []
        self.endless = endless

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        assert path == "trades"
        assert isinstance(params, dict)
        offset = params["offset"]
        assert isinstance(offset, int)
        self.requests.append(path)
        return cast(
            JsonObject,
            {
                "trades": [dict(_trade(), trade_id=offset + 101, is_open=self.endless is True)]
                if self.endless or offset == 0
                else [],
                "total_trades": 10000 if self.endless else 1,
                "offset": offset,
            },
        )


def test_redacted_own_bot_source_preview() -> None:
    source = _FakeTransport()
    result = asyncio.run(run_freqtrade_check(FreqtradeCheckConfig(), environ={}, transport=source))
    assert source.requests == ["trades"]
    assert result["closed_trade_count"] == 1
    assert result["reported_net_present"] == 1
    assert result["funding_present"] == 1
    assert result["exchange_ownership_verified"] is False
    assert result["account_financial_truth_verified"] is False
    assert result["history_complete"] is False
    assert "9876.54321" not in json.dumps(result)
    assert "BTC/USDT" not in json.dumps(result)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid:8080",
        "http://8.8.8.8:8080",
        "http://" + "user:pw@" + "127.0.0.1:8080",
        "http://127.0.0.1:8080/path",
        "http://127.0.0.1:8080/?q=1",
        "ftp://127.0.0.1:8080",
        "https://example.invalid:0",
    ],
)
def test_unsafe_origin_rejected_before_network(url: str) -> None:
    with pytest.raises(ValueError):
        FreqtradeCheckConfig(base_url=url)


@pytest.mark.parametrize("arg", ["hours", "limit", "max_pages"])
def test_unbounded_queries_rejected(arg: str) -> None:
    with pytest.raises(ValueError):
        if arg == "hours":
            FreqtradeCheckConfig(hours=0)
        elif arg == "limit":
            FreqtradeCheckConfig(limit=0)
        else:
            FreqtradeCheckConfig(max_pages=0)


def test_page_budget_fails_closed() -> None:
    source = _FakeTransport(endless=True)
    with pytest.raises(ValueError, match="page budget exceeded"):
        asyncio.run(
            run_freqtrade_check(FreqtradeCheckConfig(max_pages=2), environ={}, transport=source)
        )
    assert len(source.requests) == 2


def test_username_and_password_must_be_paired() -> None:
    with pytest.raises(ValueError, match="both"):
        asyncio.run(
            run_freqtrade_check(
                FreqtradeCheckConfig(),
                environ={"FREQTRADE_READ_USERNAME": "synthetic-operator"},
                transport=_FakeTransport(),
            )
        )


def test_cli_rejects_unsafe_url_without_network_or_secret_echo() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(
        ["freqtrade-check", "--url", "http://example.invalid:8080", "--json"],
        environ={"FREQTRADE_READ_USERNAME": "u", "FREQTRADE_READ_PASSWORD": "confidential"},
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 2
    assert stdout.getvalue() == ""
    assert "confidential" not in stderr.getvalue()


def test_real_local_http_adapter_and_basic_auth_are_get_only() -> None:
    seen: list[tuple[str, str]] = []
    username = "synthetic-operator"
    password = "-".join(("synthetic", "password"))
    expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode("ascii")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append((self.command, urlsplit(self.path).path))
            if self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.end_headers()
                return
            data = json.dumps(
                {
                    "trades": [_trade()],
                    "total_trades": 1,
                    "offset": 0,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: object) -> None:
            del fmt, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["freqtrade-check", "--url", f"http://127.0.0.1:{server.server_port}", "--json"],
            environ={
                "FREQTRADE_READ_USERNAME": username,
                "FREQTRADE_READ_PASSWORD": password,
            },
            stdout=out,
            stderr=err,
        )
        assert code == 0, err.getvalue()
        result = json.loads(out.getvalue())
        assert result["closed_trade_count"] == 1
        assert result["values_redacted"] is True
        assert result["history_complete"] is False
        assert "9876.54321" not in out.getvalue()
        assert "synthetic-operator" not in out.getvalue()
        assert seen == [("GET", "/api/v1/trades")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
