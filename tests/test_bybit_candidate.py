"""Synthetic full read-only Freqtrade/Bybit inverse two-source acceptance tests."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from freqtrade_truth.adapters.bybit_market import BybitInverseMarketInfo
from freqtrade_truth.adapters.freqtrade_order_evidence import FreqtradeOrderEvidenceError
from freqtrade_truth.adapters.http import JsonObject, StdlibJsonTransport
from freqtrade_truth.bybit_candidate import BybitCandidateConfig, compare_bybit_inverse
from freqtrade_truth.cli import main


def _market() -> BybitInverseMarketInfo:
    return BybitInverseMarketInfo(
        symbol="BTCUSD",
        base_coin="BTC",
        quote_coin="USD",
        settle_coin="BTC",
        contract_type="InversePerpetual",
        status="Trading",
        min_leverage=Decimal("1"),
        max_leverage=Decimal("100"),
        leverage_step=Decimal("1"),
        min_order_qty=Decimal("1"),
        qty_step=Decimal("1"),
        tick_size=Decimal("0.5"),
    )


async def _verified_market(*args: object, **kwargs: object) -> BybitInverseMarketInfo:
    del args, kwargs
    return _market()


class FreqtradeSource:
    def __init__(self, net: str = "0.01", *, empty: bool = False) -> None:
        self.net = net
        self.empty = empty
        self.calls: list[str] = []

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        self.calls.append(path)
        assert path == "trades"
        assert params is not None
        assert params["offset"] == 0
        return {
            "trades": []
            if self.empty
            else [
                {
                    "trade_id": 1,
                    "pair": "BTC/USD:BTC",
                    "quote_currency": "USD",
                    "is_open": False,
                    "close_timestamp": int(datetime.now(UTC).timestamp() * 1000) - 1000,
                    "profit_abs": self.net,
                    "funding_fees": None,
                }
            ],
            "total_trades": 0 if self.empty else 1,
            "offset": 0,
        }


class BybitSource:
    def __init__(self, net: str = "0.01", *, read_only: int = 1, empty: bool = False) -> None:
        self.net = net
        self.read_only = read_only
        self.empty = empty
        self.calls: list[str] = []

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        self.calls.append(path)
        if path == "v5/user/query-api":
            return {"retCode": 0, "result": {"readOnly": self.read_only}}
        assert path == "v5/position/closed-pnl"
        assert params is not None
        assert params["category"] == "inverse"
        return {
            "retCode": 0,
            "result": {
                "category": "inverse",
                "nextPageCursor": "",
                "list": []
                if self.empty
                else [
                    {
                        "symbol": "BTCUSD",
                        "orderId": "synthetic-order-1",
                        "updatedTime": str(int(datetime.now(UTC).timestamp() * 1000) - 1000),
                        "closedPnl": self.net,
                        "openFee": "0.000001",
                        "closeFee": "0.000002",
                    }
                ],
            },
        }


def _config(**kwargs: object) -> BybitCandidateConfig:
    args: dict[str, object] = {
        "freqtrade_url": "http://127.0.0.1:8080",
        "symbol": "BTCUSD",
        "base": "BTC",
        "quote": "USD",
        "settle": "BTC",
    }
    args.update(kwargs)
    return BybitCandidateConfig(**args)  # type: ignore[arg-type]


def _env() -> dict[str, str]:
    return {
        "BYBIT_API_KEY": "-".join(("synthetic", "key")),
        "BYBIT_API_SECRET": "-".join(("synthetic", "secret")),
    }


def _run(
    config: BybitCandidateConfig,
    ft: FreqtradeSource,
    ex: BybitSource,
) -> dict[str, object]:
    return asyncio.run(
        compare_bybit_inverse(
            config,
            environ=_env(),
            freqtrade_transport=ft,
            bybit_transport=ex,
            public_transport=ft,
        )
    )


def test_numeric_candidate_never_promotes_account_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual", _verified_market
    )
    ft, ex = FreqtradeSource(), BybitSource()
    result = _run(_config(), ft, ex)
    assert result["candidate_numeric_equal"] == 1
    assert result["candidate_numeric_different"] == 0
    assert result["unverified_or_ambiguous_groups"] == 0
    assert result["exchange_key_read_only_verified"] is True
    assert result["trade_identity_verified"] is False
    assert result["funding_ownership_verified"] is False
    assert result["history_complete"] is False
    assert result["account_financial_truth_verified"] is False
    assert result["financial_truth_status"] == "unverified"
    out = json.dumps(result)
    assert "synthetic-order-1" not in out
    assert "synthetic-key" not in out
    assert "BTC/USD" not in out
    assert ft.calls == ["trades"]
    assert ex.calls == ["v5/user/query-api", "v5/position/closed-pnl"]


def test_numeric_difference_is_not_silently_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual", _verified_market
    )
    result = _run(_config(), FreqtradeSource(), BybitSource("0.02"))
    assert result["candidate_numeric_equal"] == 0
    assert result["candidate_numeric_different"] == 1


def test_missing_source_becomes_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual", _verified_market
    )
    result = _run(_config(), FreqtradeSource(empty=True), BybitSource())
    assert result["candidate_numeric_equal"] == 0
    assert result["unverified_or_ambiguous_groups"] == 1


def test_trade_capable_key_rejected_before_any_private_data_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual", _verified_market
    )
    exchange = BybitSource(read_only=0)
    ft = FreqtradeSource()
    with pytest.raises(Exception, match="read-only"):
        _run(_config(), ft, exchange)
    assert ft.calls == []
    assert exchange.calls == ["v5/user/query-api"]


def test_boundary_limit_rejects_truncated_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual", _verified_market
    )
    with pytest.raises(ValueError, match="truncated"):
        _run(_config(limit=1), FreqtradeSource(), BybitSource())


@pytest.mark.parametrize(
    "override",
    [
        {"freqtrade_url": "http://example.com"},
        {"symbol": "ETHUSD"},
        {"symbol": "btcusd"},
        {"hours": 169},
        {"max_pages": 0},
        {"limit": 0},
        {"close_seconds": 3601},
        {"net_tolerance": Decimal("-1")},
        {"net_tolerance": Decimal("NaN")},
    ],
)
def test_invalid_config_never_connects(override: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _config(**override)


def test_cli_rejects_missing_exchange_key_without_leaking_private_input() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(
        ["reconcile-bybit", "--freqtrade-url", "http://127.0.0.1:8080", "--json"],
        environ={},
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 2
    assert stdout.getvalue() == ""
    assert "BYBIT" not in stderr.getvalue()


def test_bybit_collector_rejects_unapproved_endpoint() -> None:
    from freqtrade_truth.bybit_candidate import _BoundedBybit

    bybit = _BoundedBybit(BybitSource(), max_pages=1)
    with pytest.raises(ValueError, match="not allowed"):
        asyncio.run(bybit.get_json("v5/order/create"))


def test_bybit_collector_rejects_excess_pages() -> None:
    from freqtrade_truth.bybit_candidate import _BoundedBybit

    bybit = _BoundedBybit(BybitSource(), max_pages=1)
    asyncio.run(bybit.get_json("v5/user/query-api"))
    asyncio.run(bybit.get_json("v5/position/closed-pnl", {"category": "inverse"}))
    with pytest.raises(ValueError, match="page budget"):
        asyncio.run(bybit.get_json("v5/position/closed-pnl", {"category": "inverse"}))


def test_empty_sources_keep_all_ownership_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    result = _run(_config(), FreqtradeSource(empty=True), BybitSource(empty=True))
    assert result["candidate_numeric_equal"] == 0
    assert result["comparison_groups"] == 0
    assert result["history_complete"] is False
    assert result["account_financial_truth_verified"] is False


def test_opt_in_values_are_native_and_not_account_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    output = _run(
        _config(include_values=True),
        FreqtradeSource(net="0.01"),
        BybitSource(net="0.02"),
    )
    assert output["values_redacted"] is False
    totals = output["source_window_totals"]
    assert isinstance(totals, dict)
    assert totals["currency"] == "BTC"
    assert totals["freqtrade_reported_net"] == "0.01"
    assert totals["bybit_reported_net"] == "0.02"
    assert totals["difference_bybit_minus_freqtrade"] == "0.01"
    assert output["account_financial_truth_verified"] is False


def test_missing_values_never_report_zero_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    result = _run(
        _config(include_values=True),
        FreqtradeSource(empty=True),
        BybitSource(net="0.01"),
    )
    totals = result["source_window_totals"]
    assert isinstance(totals, dict)
    assert totals["freqtrade_reported_net"] is None
    assert totals["difference_bybit_minus_freqtrade"] is None


def _order_stats(report: dict[str, object]) -> dict[str, object]:
    value = report["order_reference_review"]
    assert isinstance(value, dict)
    return value


class DetailFreqtradeSource(FreqtradeSource):
    """Synthetic official GET /trade/<id> evidence, never real bot data."""

    def __init__(
        self,
        *,
        filled: str | None = "0.5",
        order_id: str = "synthetic-order-1",
        detail_id: int = 1,
        missing_orders: bool = False,
    ) -> None:
        super().__init__()
        self.filled = filled
        self.order_id = order_id
        self.detail_id = detail_id
        self.missing_orders = missing_orders

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        if path == "trade/1":
            assert params is None
            self.calls.append(path)
            out: JsonObject = {
                "trade_id": self.detail_id,
                "pair": "BTC/USD:BTC",
                "is_open": False,
            }
            if not self.missing_orders:
                out["orders"] = [
                    {
                        "order_id": self.order_id,
                        "pair": "BTC/USD:BTC",
                        "filled": Decimal(self.filled) if self.filled is not None else None,
                    }
                ]
            return out
        return await super().get_json(path, params)


def test_exact_positive_filled_order_reference_only_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    ft, ex = DetailFreqtradeSource(), BybitSource()
    result = _run(_config(check_order_refs=True), ft, ex)
    assert _order_stats(result)["checked_bot_trades"] == 1
    assert _order_stats(result)["single_candidate"] == 1
    assert _order_stats(result)["no_candidate"] == 0
    assert _order_stats(result)["ambiguous"] == 0
    assert result["trade_identity_verified"] is False
    assert result["account_financial_truth_verified"] is False
    assert ft.calls == ["trades", "trade/1"]
    assert ex.calls == ["v5/user/query-api", "v5/position/closed-pnl"]
    assert "synthetic-order-1" not in json.dumps(result)


@pytest.mark.parametrize("filled", ["0", None])
def test_zero_or_unknown_fill_not_a_candidate(
    monkeypatch: pytest.MonkeyPatch,
    filled: str | None,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    result = _run(
        _config(check_order_refs=True), DetailFreqtradeSource(filled=filled), BybitSource()
    )
    assert _order_stats(result)["single_candidate"] == 0
    assert _order_stats(result)["no_candidate"] == 1


def test_different_order_id_no_candidate_even_equal_net(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    result = _run(
        _config(check_order_refs=True),
        DetailFreqtradeSource(order_id="another-order"),
        BybitSource(),
    )
    assert result["candidate_numeric_equal"] == 1
    assert _order_stats(result)["single_candidate"] == 0
    assert _order_stats(result)["no_candidate"] == 1
    assert result["trade_identity_verified"] is False


def test_missing_or_mismatched_trade_detail_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    with pytest.raises(FreqtradeOrderEvidenceError):
        _run(_config(check_order_refs=True), DetailFreqtradeSource(detail_id=2), BybitSource())
    with pytest.raises(FreqtradeOrderEvidenceError):
        _run(
            _config(check_order_refs=True),
            DetailFreqtradeSource(missing_orders=True),
            BybitSource(),
        )


def test_disabled_order_review_does_not_read_private_trade_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    ft = DetailFreqtradeSource()
    result = _run(_config(check_order_refs=False), ft, BybitSource())
    assert result["order_reference_review"] is None
    assert ft.calls == ["trades"]


def test_order_review_config_must_be_boolean() -> None:
    with pytest.raises(ValueError):
        _config(check_order_refs="yes")


def test_order_review_cli_option_available() -> None:
    from freqtrade_truth.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["reconcile-bybit", "--check-order-links"])
    assert args.check_order_links is True


class CompetingFreqtradeSource(DetailFreqtradeSource):
    """Two bot trades claim the exact same source exchange order ID."""

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        if path == "trades":
            raw = await super().get_json(path, params)
            trades = raw["trades"]
            assert isinstance(trades, list)
            trades.append(
                {
                    "trade_id": 2,
                    "pair": "BTC/USD:BTC",
                    "quote_currency": "USD",
                    "is_open": False,
                    "close_timestamp": int(datetime.now(UTC).timestamp() * 1000) - 900,
                    "profit_abs": "0.01",
                    "funding_fees": None,
                }
            )
            raw["total_trades"] = 2
            return raw
        if path == "trade/2":
            self.calls.append(path)
            return {
                "trade_id": 2,
                "pair": "BTC/USD:BTC",
                "is_open": False,
                "orders": [
                    {
                        "order_id": "synthetic-order-1",
                        "pair": "BTC/USD:BTC",
                        "filled": Decimal("0.1"),
                    }
                ],
            }
        return await super().get_json(path, params)


def test_two_bot_trades_compete_for_same_order_without_claiming_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    ft = CompetingFreqtradeSource()
    result = _run(_config(check_order_refs=True), ft, BybitSource())
    review = _order_stats(result)
    assert review["checked_bot_trades"] == 2
    assert review["single_candidate"] == 0
    assert review["ambiguous"] == 2
    assert review["verified_trade_ownership"] is False
    assert result["account_financial_truth_verified"] is False
    assert ft.calls == ["trades", "trade/1", "trade/2"]


def test_detail_gate_rejects_extra_or_repeat_requests() -> None:
    from freqtrade_truth.bybit_candidate import _DetailOnlyTransport

    source = DetailFreqtradeSource()
    gate = _DetailOnlyTransport(source, frozenset({1}))
    with pytest.raises(ValueError, match="unapproved"):
        asyncio.run(gate.get_json("forceenter"))
    with pytest.raises(ValueError, match="unapproved"):
        asyncio.run(gate.get_json("trade/2"))
    asyncio.run(gate.get_json("trade/1"))
    with pytest.raises(ValueError, match="repeated"):
        asyncio.run(gate.get_json("trade/1"))
    assert source.calls == ["trade/1"]


def test_real_local_freqtrade_http_basic_auth_with_two_source_order_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic exchange side; real loopback GET + Basic Auth on Freqtrade side."""

    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    username = "-".join(("synthetic", "reader"))
    password = "-".join(("synthetic", "local", "password"))
    auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    observed: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append(self.path.split("?")[0])
            if self.headers.get("Authorization") != auth:
                self.send_response(401)
                self.end_headers()
                return
            path = self.path.split("?")[0]
            if path == "/api/v1/trades":
                data: dict[str, object] = {
                    "trades": [
                        {
                            "trade_id": 1,
                            "pair": "BTC/USD:BTC",
                            "quote_currency": "USD",
                            "is_open": False,
                            "close_timestamp": int(datetime.now(UTC).timestamp() * 1000) - 1000,
                            "profit_abs": "0.01",
                            "funding_fees": None,
                        }
                    ],
                    "total_trades": 1,
                    "offset": 0,
                }
            elif path == "/api/v1/trade/1":
                data = {
                    "trade_id": 1,
                    "pair": "BTC/USD:BTC",
                    "is_open": False,
                    "orders": [
                        {
                            "order_id": "synthetic-order-1",
                            "pair": "BTC/USD:BTC",
                            "filled": 0.5,
                        }
                    ],
                }
            else:
                self.send_response(404)
                self.end_headers()
                return
            encoded = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, fmt: str, *args: object) -> None:
            del fmt, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        ft_transport = StdlibJsonTransport(base_url, username=username, password=password)
        output = asyncio.run(
            compare_bybit_inverse(
                _config(freqtrade_url=base_url, check_order_refs=True),
                environ=_env()
                | {
                    "FREQTRADE_READ_USERNAME": username,
                    "FREQTRADE_READ_PASSWORD": password,
                },
                freqtrade_transport=ft_transport,
                bybit_transport=BybitSource(),
                public_transport=FreqtradeSource(),
            )
        )
        review = _order_stats(output)
        assert review["single_candidate"] == 1
        assert output["trade_identity_verified"] is False
        assert output["account_financial_truth_verified"] is False
        assert observed == ["/api/v1/trades", "/api/v1/trade/1"]
        assert username not in json.dumps(output)
        assert password not in json.dumps(output)
        assert "synthetic-order-1" not in json.dumps(output)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


class ThreeSourceBybit(BybitSource):
    """Fully synthetic Bybit GET-only execution and account-ledger fixture."""

    def __init__(self, *, missing_execution: bool = False, wrong_fee: bool = False) -> None:
        super().__init__()
        self.missing_execution = missing_execution
        self.wrong_fee = wrong_fee

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        if path == "v5/execution/list":
            self.calls.append(path)
            assert params is not None and params["orderId"] == "synthetic-order-1"
            assert params["execType"] == "Trade"
            fill: JsonObject = {
                "execId": "synthetic-fill-1",
                "orderId": "synthetic-order-1",
                "symbol": "BTCUSD",
                "execType": "Trade",
                "execQty": "0.5",
                "execTime": str(int(datetime.now(UTC).timestamp() * 1000) - 1000),
                "execFee": "0.000003",
                "feeCurrency": "BTC",
            }
            return {
                "retCode": 0,
                "result": {
                    "category": "inverse",
                    "list": [] if self.missing_execution else [fill],
                    "nextPageCursor": "",
                },
            }
        if path == "v5/account/transaction-log":
            self.calls.append(path)
            assert params is not None
            ledger: JsonObject = {
                "id": "synthetic-ledger-1",
                "symbol": "BTCUSD",
                "category": "inverse",
                "transactionTime": str(int(datetime.now(UTC).timestamp() * 1000) - 1000),
                "type": "TRADE",
                "currency": "BTC",
                "funding": "0",
                "fee": "0.000004" if self.wrong_fee else "0.000003",
                "cashFlow": "0.01",
                "change": "0.009996" if self.wrong_fee else "0.009997",
                "orderId": "synthetic-order-1",
                "tradeId": "synthetic-fill-1",
            }
            return {
                "retCode": 0,
                "result": {"list": [ledger], "nextPageCursor": ""},
            }
        return await super().get_json(path, params)


def test_three_source_execution_fee_ledger_chain_candidate_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    bot = DetailFreqtradeSource()
    source = ThreeSourceBybit()
    result = _run(_config(check_order_refs=True, check_execution_ledger=True), bot, source)
    check = result["execution_ledger_review"]
    assert isinstance(check, dict)
    assert check["candidate_trades"] == 1
    assert check["ambiguous_trades"] == 0
    assert check["checked_orders"] == 1
    assert check["pagination_exhausted"] is True
    assert check["verified_account_ownership"] is False
    assert check["verified_full_funding"] is False
    assert result["account_financial_truth_verified"] is False
    assert "synthetic-fill-1" not in json.dumps(result)
    assert source.calls == [
        "v5/user/query-api",
        "v5/position/closed-pnl",
        "v5/execution/list",
        "v5/account/transaction-log",
    ]


@pytest.mark.parametrize(
    "missing_execution,wrong_fee",
    [(True, False), (False, True)],
)
def test_three_source_missing_fill_or_fee_mismatch_stays_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    missing_execution: bool,
    wrong_fee: bool,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    result = _run(
        _config(check_order_refs=True, check_execution_ledger=True),
        DetailFreqtradeSource(),
        ThreeSourceBybit(missing_execution=missing_execution, wrong_fee=wrong_fee),
    )
    check = result["execution_ledger_review"]
    assert isinstance(check, dict)
    assert check["candidate_trades"] == 0
    assert check["ambiguous_trades"] == 1


def test_three_source_deep_mode_requires_order_detail_mode() -> None:
    with pytest.raises(ValueError):
        _config(check_execution_ledger=True)


def test_three_source_rejects_write_enabled_key_before_account_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    source = ThreeSourceBybit()
    source.read_only = 0
    with pytest.raises(Exception, match="read-only"):
        _run(
            _config(check_order_refs=True, check_execution_ledger=True),
            DetailFreqtradeSource(),
            source,
        )
    assert source.calls == ["v5/user/query-api"]


def test_three_source_conflicting_bot_claims_remain_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    result = _run(
        _config(check_order_refs=True, check_execution_ledger=True),
        CompetingFreqtradeSource(),
        ThreeSourceBybit(),
    )
    check = result["execution_ledger_review"]
    assert isinstance(check, dict)
    assert check["candidate_trades"] == 0
    assert check["ambiguous_trades"] == 2
    assert check["verified_account_ownership"] is False


def test_three_source_source_page_budget_is_global_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    exchange = ThreeSourceBybit()
    with pytest.raises(ValueError, match="page budget"):
        _run(
            _config(check_order_refs=True, check_execution_ledger=True, max_pages=2),
            DetailFreqtradeSource(),
            exchange,
        )
    assert exchange.calls == [
        "v5/user/query-api",
        "v5/position/closed-pnl",
        "v5/execution/list",
    ]


def test_three_source_disabled_by_default_fetches_no_ledger_or_executions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "freqtrade_truth.bybit_candidate.verify_bybit_inverse_perpetual",
        _verified_market,
    )
    exchange = ThreeSourceBybit()
    result = _run(_config(check_order_refs=True), DetailFreqtradeSource(), exchange)
    assert result["execution_ledger_review"] is None
    assert exchange.calls == ["v5/user/query-api", "v5/position/closed-pnl"]


def test_ledger_flag_is_reviewed_read_only_cli_option() -> None:
    from freqtrade_truth.cli import build_parser

    args = build_parser().parse_args(
        ["reconcile-bybit", "--check-order-links", "--check-execution-ledger"]
    )
    assert args.check_order_links is True
    assert args.check_execution_ledger is True


def test_mutation_endpoint_is_not_in_authenticated_bybit_allowlist() -> None:
    from freqtrade_truth.adapters.bybit_auth import _validate_api_path

    assert _validate_api_path("v5/execution/list") == "v5/execution/list"
    with pytest.raises(ValueError, match="allowlisted"):
        _validate_api_path("v5/order/create")
