"""Synthetic, read-only OKX position history adapter tests (no account/network)."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from freqtrade_truth.adapters.http import JsonObject, JsonValue
from freqtrade_truth.adapters.okx_positions_history import (
    OkxPositionHistoryAdapter,
    OkxPositionHistoryError,
)
from freqtrade_truth.core.models import ClosedTradeQuery, FinancialField, SourceKind

OPEN = datetime(2030, 1, 1, tzinfo=UTC)
END = datetime(2030, 1, 4, tzinfo=UTC)


def item(
    *,
    pos_id: str = "pos1",
    ts: datetime = OPEN + timedelta(days=1),
    inst_id: str = "BTC-USD-310404",
    ccy: str = "USD",
    typ: str = "2",
    pnl: str = "12.0",
    fee: str = "-2.0",
    funding: str = "-0.5",
    penalty: str = "0",
    settled: str = "0",
    net: str = "9.5",
) -> dict[str, JsonValue]:
    return {
        "posId": pos_id,
        "cTime": str(int(OPEN.timestamp() * 1000)),
        "uTime": str(int(ts.timestamp() * 1000)),
        "instId": inst_id,
        "instType": "FUTURES",
        "ccy": ccy,
        "type": typ,
        "direction": "long",
        "pnl": pnl,
        "fee": fee,
        "fundingFee": funding,
        "liqPenalty": penalty,
        "settledPnl": settled,
        "realizedPnl": net,
    }


def response(*records: dict[str, JsonValue], code: str = "0") -> JsonObject:
    return {"code": code, "msg": "", "data": [dict(r) for r in records]}


class FakeTransport:
    def __init__(self, *pages: JsonObject):
        self.pages = list(pages)
        self.calls: list[tuple[str, dict[str, str | int | bool]]] = []

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        self.calls.append((path, dict(params or {})))
        if not self.pages:
            raise AssertionError("unexpected extra synthetic HTTP page")
        return self.pages.pop(0)


def reader(transport: FakeTransport) -> OkxPositionHistoryAdapter:
    return OkxPositionHistoryAdapter(
        transport,
        inst_id="BTC-USD-310404",
        inst_type="FUTURES",
        settlement_currency="USD",
        normalized_instrument="BTC/USD:USD-310404",
        page_size=2,
    )


def query() -> ClosedTradeQuery:
    return ClosedTradeQuery(
        closed_from=OPEN,
        closed_until=END,
        instrument="BTC/USD:USD-310404",
    )


def test_read_only_okx_full_close_fields_not_double_counted() -> None:
    t = FakeTransport(response(item()))
    adapter = reader(t)
    rows = asyncio.run(adapter.fetch_closed_trades(query()))
    assert adapter.name == "okx"
    assert adapter.source_kind is SourceKind.EXCHANGE
    assert set(adapter.capabilities().fields) == set(FinancialField)
    assert len(rows) == 1
    row = rows[0]
    assert row.trade_ref == "pos1"
    assert row.instrument == "BTC/USD:USD-310404"
    assert row.settlement_currency == "USD"
    assert row.price_pnl == Decimal("12.0")
    assert row.trading_fees == Decimal("-2.0")
    assert row.funding == Decimal("-0.5")
    assert row.other_adjustments == Decimal("0")
    assert row.reported_net_pnl == Decimal("9.5")
    assert t.calls == [
        (
            "api/v5/account/positions-history",
            {"instType": "FUTURES", "instId": "BTC-USD-310404", "limit": 2},
        )
    ]


def test_okx_rebates_and_negative_funding_preserve_native_signs() -> None:
    t = FakeTransport(response(item(pnl="1", fee="0.2", funding="-0.3", net="0.9")))
    rows = asyncio.run(reader(t).fetch_closed_trades(query()))
    assert rows[0].trading_fees == Decimal("0.2")
    assert rows[0].funding == Decimal("-0.3")
    assert rows[0].reported_net_pnl == Decimal("0.9")


def test_unknown_component_keeps_unknown_but_source_reported_net() -> None:
    t = FakeTransport(response(item(funding="", net="9.5")))
    rows = asyncio.run(reader(t).fetch_closed_trades(query()))
    assert rows[0].funding is None
    assert rows[0].reported_net_pnl == Decimal("9.5")


def test_only_full_close_is_reported_no_partial_promoted() -> None:
    t = FakeTransport(response(item(typ="1"), item(pos_id="full")), response())
    rows = asyncio.run(reader(t).fetch_closed_trades(query()))
    assert [r.trade_ref for r in rows] == ["full"]


def test_pagination_uses_oldest_utime_and_exhausts_full_pages() -> None:
    later = OPEN + timedelta(days=3)
    earlier = OPEN + timedelta(days=2)
    t = FakeTransport(
        response(item(pos_id="later", ts=later), item(pos_id="earlier", ts=earlier)),
        response(item(pos_id="first", ts=OPEN + timedelta(days=1))),
    )
    rows = asyncio.run(reader(t).fetch_closed_trades(query()))
    assert [r.trade_ref for r in rows] == ["first", "earlier", "later"]
    assert t.calls[1][1]["after"] == str(int(earlier.timestamp() * 1000))


def test_first_page_outside_window_stops_without_false_history_claim() -> None:
    t = FakeTransport(response(item(ts=OPEN - timedelta(days=1))))
    rows = asyncio.run(reader(t).fetch_closed_trades(query()))
    assert rows == ()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"instId": "ETH-USD-310404"}, "instId"),
        ({"instType": "SWAP"}, "instType"),
        ({"ccy": "BTC"}, "ccy"),
        ({"realizedPnl": "NaN"}, "realizedPnl"),
        ({"realizedPnl": "Infinity"}, "realizedPnl"),
        ({"pnl": "not-numeric"}, "pnl"),
        ({"uTime": "wrong"}, "uTime"),
        ({"posId": ""}, "posId"),
        ({"realizedPnl": "99.0"}, "equation"),
    ],
)
def test_malformed_or_inconsistent_row_fails_closed(
    change: dict[str, JsonValue], message: str
) -> None:
    raw = item()
    raw.update(change)
    t = FakeTransport(response(raw))
    with pytest.raises(OkxPositionHistoryError, match=message):
        asyncio.run(reader(t).fetch_closed_trades(query()))


def test_api_failure_rejected_not_silently_empty() -> None:
    with pytest.raises(OkxPositionHistoryError, match="code"):
        asyncio.run(reader(FakeTransport(response(code="50011"))).fetch_closed_trades(query()))


def test_duplicate_source_event_fails_closed() -> None:
    t = FakeTransport(response(item(), item()))
    with pytest.raises(OkxPositionHistoryError, match="duplicate"):
        asyncio.run(reader(t).fetch_closed_trades(query()))


def test_ambiguous_pagination_timestamp_fails_closed() -> None:
    at = OPEN + timedelta(days=2)
    t = FakeTransport(
        response(item(pos_id="x", ts=at), item(pos_id="y", ts=at)),
        response(item(pos_id="z", ts=at), item(pos_id="w", ts=at)),
    )
    with pytest.raises(OkxPositionHistoryError, match="pagination"):
        asyncio.run(reader(t).fetch_closed_trades(query()))


def test_query_other_instrument_never_calls_exchange() -> None:
    t = FakeTransport()
    q = ClosedTradeQuery(closed_from=OPEN, closed_until=END, instrument="ETH/USD:USD-310404")
    assert asyncio.run(reader(t).fetch_closed_trades(q)) == ()
    assert t.calls == []


def test_public_adapter_does_not_require_read_account_secrets() -> None:
    t = FakeTransport(response())
    assert asyncio.run(reader(t).fetch_closed_trades(query())) == ()
    assert all(path == "api/v5/account/positions-history" for path, _ in t.calls)
