"""Synthetic regression cases for provisional Freqtrade order-ID evidence (#15)."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from freqtrade_truth.adapters.freqtrade_order_evidence import (
    FreqtradeClosedTradeOrderEvidence,
    FreqtradeClosedTradeOrderReader,
    FreqtradeOrderEvidenceError,
)
from freqtrade_truth.adapters.http import JsonObject, JsonValue
from freqtrade_truth.core import (
    ComparableRecordGroup,
    FinancialField,
    NormalizedTradeRecord,
    ReconciliationStatus,
    ReconciliationTolerance,
    SourceKind,
    reconcile_group,
)


class SingleResponseTransport:
    def __init__(self, response: JsonObject) -> None:
        self.response = response
        self.paths: list[str] = []

    async def get_json(self, path: str, params: object = None) -> JsonObject:
        assert params is None
        self.paths.append(path)
        return self.response


def trade_detail() -> JsonObject:
    return {
        "trade_id": 42,
        "pair": "BTC/USD:BTC",
        "is_open": False,
        "orders": [
            {"order_id": "EX-ENTRY-2", "pair": "BTC/USD:BTC", "filled": Decimal("0.5")},
            {
                "order_id": "EX-EXIT-1",
                "pair": "BTC/USD:BTC",
                "filled": Decimal("0.1"),
                "status": "canceled",
            },
            {
                "order_id": "EX-ENTRY-1",
                "pair": "BTC/USD:BTC",
                "filled": Decimal("0"),
                "status": "canceled",
            },
        ],
    }


def read(transport: SingleResponseTransport) -> FreqtradeClosedTradeOrderEvidence:
    return asyncio.run(
        FreqtradeClosedTradeOrderReader(transport, source_name="synthetic-bot").fetch(
            42, expected_instrument="BTC/USD:BTC"
        )
    )


def test_order_evidence_is_read_only_and_deterministically_sorted() -> None:
    transport = SingleResponseTransport(trade_detail())
    snapshot = read(transport)

    assert transport.paths == ["trade/42"]
    assert snapshot.source_name == "synthetic-bot"
    assert snapshot.trade_ref == "42"
    assert snapshot.instrument == "BTC/USD:BTC"
    assert snapshot.observed_exchange_order_ids == (
        "EX-ENTRY-1",
        "EX-ENTRY-2",
        "EX-EXIT-1",
    )
    assert snapshot.positive_fill_exchange_order_ids == ("EX-ENTRY-2", "EX-EXIT-1")
    assert snapshot.zero_fill_exchange_order_ids == ("EX-ENTRY-1",)
    assert snapshot.unknown_fill_exchange_order_ids == ()


def test_empty_order_list_remains_empty_evidence_not_invented_link() -> None:
    payload = trade_detail()
    payload["orders"] = []
    snapshot = read(SingleResponseTransport(payload))
    assert snapshot.observed_exchange_order_ids == ()


def test_missing_and_null_fill_values_are_unknown_not_zero() -> None:
    payload = trade_detail()
    payload["orders"] = [
        {"order_id": "EX-MISSING"},
        {"order_id": "EX-NULL", "filled": None},
        {"order_id": "EX-ZERO", "filled": 0},
    ]
    result = read(SingleResponseTransport(payload))
    assert result.positive_fill_exchange_order_ids == ()
    assert result.zero_fill_exchange_order_ids == ("EX-ZERO",)
    assert result.unknown_fill_exchange_order_ids == ("EX-MISSING", "EX-NULL")


def test_positive_fill_on_cancelled_order_is_not_discarded() -> None:
    payload = trade_detail()
    payload["orders"] = [
        {"order_id": "EX-PARTIAL", "filled": Decimal("0.001"), "status": "canceled"},
        {"order_id": "EX-CANCELLED", "filled": Decimal("0"), "status": "canceled"},
    ]
    result = read(SingleResponseTransport(payload))
    assert result.positive_fill_exchange_order_ids == ("EX-PARTIAL",)
    assert result.zero_fill_exchange_order_ids == ("EX-CANCELLED",)


@pytest.mark.parametrize("bad_filled", [True, "1.2", Decimal("-0.1"), Decimal("Infinity")])
def test_invalid_fill_amount_is_rejected(bad_filled: object) -> None:
    payload = trade_detail()
    payload["orders"] = [{"order_id": "EX-BAD", "filled": cast(JsonValue, bad_filled)}]
    with pytest.raises(FreqtradeOrderEvidenceError, match="filled"):
        read(SingleResponseTransport(payload))


@pytest.mark.parametrize("trade_id", [0, -1, True, 1.0, "42"])
def test_invalid_trade_id_is_rejected_before_request(trade_id: object) -> None:
    transport = SingleResponseTransport(trade_detail())
    with pytest.raises(ValueError, match="positive integer"):
        asyncio.run(
            FreqtradeClosedTradeOrderReader(transport).fetch(
                cast(int, trade_id), expected_instrument="BTC/USD:BTC"
            )
        )
    assert transport.paths == []


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("trade_id", 43, "ID does not match"),
        ("trade_id", True, "ID does not match"),
        ("pair", "ETH/USD:BTC", "instrument does not match"),
        ("is_open", True, "explicitly closed"),
        ("is_open", None, "explicitly closed"),
        ("orders", None, "orders must be an array"),
        ("orders", [None], "order must be an object"),
        ("orders", [{"order_id": None}], "order_id"),
        ("orders", [{"order_id": ""}], "order_id"),
        ("orders", [{"order_id": "EX-1", "pair": "ETH/USD:BTC"}], "instrument mismatch"),
        ("orders", [{"order_id": "EX-1"}, {"order_id": "EX-1"}], "duplicate"),
    ],
)
def test_malformed_or_ambiguous_trade_detail_fails_closed(
    key: str, value: object, message: str
) -> None:
    payload = trade_detail()
    payload[key] = value  # type: ignore[assignment]
    with pytest.raises(FreqtradeOrderEvidenceError, match=message):
        read(SingleResponseTransport(payload))


def test_no_cross_trade_ownership_inferred_from_close_time() -> None:
    snapshot = read(SingleResponseTransport(trade_detail()))
    # A foreign exchange order can close at the same time with the same PnL.
    assert "EX-FOREIGN" not in snapshot.observed_exchange_order_ids
    assert "EX-EXIT-1" in snapshot.observed_exchange_order_ids


def _financial_record(
    source_kind: SourceKind, record_id: str, trade_ref: str
) -> NormalizedTradeRecord:
    return NormalizedTradeRecord(
        record_id=record_id,
        source_kind=source_kind,
        source_name="synthetic-bot" if source_kind is SourceKind.FREQTRADE else "synthetic-bybit",
        trade_ref=trade_ref,
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        closed_at=datetime(2030, 1, 1, 12, 0, tzinfo=UTC),
        reported_net_pnl=Decimal("0.001"),
    )


@pytest.mark.xfail(
    strict=True,
    reason="#15: preview time-only numeric MATCH is not verified cross-source ownership",
)
def test_unrelated_same_time_order_must_not_be_finally_verified() -> None:
    """Known preview limitation; will become an ordinary test when #15 is implemented."""

    bot = _financial_record(SourceKind.FREQTRADE, "freqtrade:42", "42")
    exchange = _financial_record(SourceKind.EXCHANGE, "bybit:EX-FOREIGN", "EX-FOREIGN")
    group = ComparableRecordGroup(
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        freqtrade_records=(bot,),
        exchange_records=(exchange,),
    )
    result = reconcile_group(
        group,
        tolerance=ReconciliationTolerance(),
        fields=(FinancialField.REPORTED_NET_PNL,),
    )
    assert result.status is not ReconciliationStatus.MATCH


def test_source_records_remain_immutable() -> None:
    original = _financial_record(SourceKind.FREQTRADE, "freqtrade:42", "42")
    changed = replace(original, reported_net_pnl=Decimal("0.002"))
    assert original.reported_net_pnl == Decimal("0.001")
    assert changed.reported_net_pnl == Decimal("0.002")
