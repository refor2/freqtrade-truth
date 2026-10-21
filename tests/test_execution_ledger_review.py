"""Synthetic cross-source execId ↔ ledger tradeId corroboration; candidate only."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from freqtrade_truth.adapters.bybit_execution_evidence import (
    BybitExecutionWindowEvidence,
    BybitTradeExecution,
)
from freqtrade_truth.adapters.bybit_transaction_log import (
    BybitPaginatedWindowEvidence,
    BybitTransactionLogEntry,
)
from freqtrade_truth.adapters.execution_ledger_review import (
    review_bot_execution_ledger_candidates,
    review_execution_ledger_candidates,
)
from freqtrade_truth.adapters.freqtrade_order_evidence import FreqtradeClosedTradeOrderEvidence
from freqtrade_truth.adapters.order_link_review import OrderLinkReviewState

START = datetime(2030, 1, 1, tzinfo=UTC)
END = START + timedelta(days=1)


def fill(
    exec_id: str = "EX-1",
    order_id: str = "ORDER",
    fee: Decimal | None = Decimal("0.02"),
    currency: str | None = "BTC",
) -> BybitTradeExecution:
    return BybitTradeExecution(
        execution_ref=exec_id,
        order_ref=order_id,
        symbol="BTCUSD",
        occurred_at=START + timedelta(hours=12),
        executed_qty=Decimal("2"),
        source_exec_fee=fee,
        fee_currency=currency,
    )


def fills(
    *items: BybitTradeExecution,
    start: datetime = START,
    end: datetime = END,
    exhausted: bool = True,
) -> BybitExecutionWindowEvidence:
    return BybitExecutionWindowEvidence(
        source_name="bybit",
        order_ref="ORDER",
        symbol="BTCUSD",
        settlement_currency="BTC",
        start_at=start,
        end_at=end,
        records=items,
        pagination_exhausted=exhausted,
    )


def ledger_event(
    exec_id: str = "EX-1",
    order_id: str = "ORDER",
    fee: Decimal | None = Decimal("-0.02"),
    kind: str = "TRADE",
    instrument: str | None = "BTCUSD",
    ccy: str = "BTC",
    record_id: str = "LOG-1",
) -> BybitTransactionLogEntry:
    return BybitTransactionLogEntry(
        record_id=record_id,
        event_ref=record_id,
        source_name="bybit",
        trade_ref=exec_id,
        order_ref=order_id,
        event_type=kind,
        instrument=instrument,
        settlement_currency=ccy,
        occurred_at=START + timedelta(hours=12),
        funding=None,
        trading_fees=fee,
        cash_flow=Decimal("0"),
        reported_net_change=Decimal("0"),
    )


def ledger(
    *items: BybitTransactionLogEntry,
    start: datetime = START,
    end: datetime = END,
    exhausted: bool = True,
) -> BybitPaginatedWindowEvidence:
    return BybitPaginatedWindowEvidence(
        source_name="bybit",
        native_symbol="BTCUSD",
        settlement_currency="BTC",
        start_at=start,
        end_at=end,
        records=items,
        pagination_exhausted=exhausted,
    )


def test_matching_execution_tradeid_order_fee_and_currency_yields_candidate_only() -> None:
    results = review_execution_ledger_candidates((fills(fill()),), ledger(ledger_event()))
    assert len(results) == 1
    assert results[0].trade_ref == "EX-1"
    assert results[0].state is OrderLinkReviewState.CANDIDATE
    assert results[0].candidate_record_ids == ("LOG-1",)
    assert not hasattr(results[0], "verified_owner")
    assert not hasattr(results[0], "trading_fee_total")


def test_two_fills_same_order_link_by_execution_id_not_order_id() -> None:
    rows = review_execution_ledger_candidates(
        (fills(fill("EX-2"), fill("EX-1")),),
        ledger(ledger_event("EX-2", record_id="LOG-2"), ledger_event("EX-1")),
    )
    assert [(x.trade_ref, x.candidate_record_ids) for x in rows] == [
        ("EX-1", ("LOG-1",)),
        ("EX-2", ("LOG-2",)),
    ]


def test_order_match_but_different_execid_does_not_link() -> None:
    rows = review_execution_ledger_candidates((fills(fill()),), ledger(ledger_event("OTHER")))
    assert rows[0].state is OrderLinkReviewState.NO_CANDIDATE


@pytest.mark.parametrize(
    "change",
    [
        {"order_id": "OTHER"},
        {"fee": Decimal("-0.03")},
        {"ccy": "USD"},
        {"instrument": None},
        {"instrument": "ETHUSD"},
    ],
)
def test_conflicting_provenance_or_fee_never_produces_candidate(change: dict[str, object]) -> None:
    args: dict[str, object] = dict(exec_id="EX-1", record_id="LOG-1")
    args.update(change)
    row = ledger_event(**args)  # type: ignore[arg-type]
    results = review_execution_ledger_candidates((fills(fill()),), ledger(row))
    assert results[0].state is OrderLinkReviewState.AMBIGUOUS
    assert results[0].candidate_record_ids == ()


def test_fee_currency_unknown_and_missing_amount_are_incomplete_not_zero() -> None:
    for execution in (fill(currency=None), fill(fee=None)):
        result = review_execution_ledger_candidates((fills(execution),), ledger(ledger_event()))[0]
        assert result.state is OrderLinkReviewState.AMBIGUOUS


def test_settlement_record_with_same_tradeid_is_not_trade_fee_candidate() -> None:
    rows = review_execution_ledger_candidates(
        (fills(fill()),), ledger(ledger_event(kind="SETTLEMENT"))
    )
    assert rows[0].state is OrderLinkReviewState.NO_CANDIDATE


def test_duplicate_tradeid_or_duplicate_execution_id_fails_closed() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        review_execution_ledger_candidates(
            (fills(fill()),), ledger(ledger_event(), ledger_event(record_id="LOG-2"))
        )
    with pytest.raises(ValueError, match="duplicate"):
        review_execution_ledger_candidates((fills(fill(), fill()),), ledger(ledger_event()))


def test_truncated_pagination_and_lifecycle_window_never_qualifies() -> None:
    for lhs, rhs in (
        (fills(fill(), exhausted=False), ledger(ledger_event())),
        (fills(fill()), ledger(ledger_event(), exhausted=False)),
        (fills(fill()), ledger(ledger_event(), start=START + timedelta(hours=1))),
    ):
        with pytest.raises(ValueError, match="coverage"):
            review_execution_ledger_candidates((lhs,), rhs)


def test_arbitrary_source_order_does_not_change_candidates() -> None:
    a = fills(fill("EX-2"), fill("EX-1"))
    b = fills(fill("EX-1"), fill("EX-2"))
    logs = ledger(ledger_event("EX-2", record_id="LOG-2"), ledger_event("EX-1"))
    logs_reverse = ledger(ledger_event("EX-1"), ledger_event("EX-2", record_id="LOG-2"))
    assert review_execution_ledger_candidates((a,), logs) == review_execution_ledger_candidates(
        (b,), logs_reverse
    )


def bot_snapshot(
    trade_id: str,
    *,
    positive: tuple[str, ...] = (),
    unknown: tuple[str, ...] = (),
    zero: tuple[str, ...] = (),
    instrument: str = "BTC/USD:BTC",
) -> FreqtradeClosedTradeOrderEvidence:
    return FreqtradeClosedTradeOrderEvidence(
        source_name="freqtrade-synthetic",
        trade_ref=trade_id,
        instrument=instrument,
        observed_exchange_order_ids=tuple(sorted((*positive, *unknown, *zero))),
        positive_fill_exchange_order_ids=positive,
        unknown_fill_exchange_order_ids=unknown,
        zero_fill_exchange_order_ids=zero,
    )


def test_three_source_order_execution_ledger_chain_is_only_candidate() -> None:
    result = review_bot_execution_ledger_candidates(
        (bot_snapshot("42", positive=("ORDER",)),),
        (fills(fill("EX-1"), fill("EX-2")),),
        ledger(ledger_event("EX-2", record_id="LOG-2"), ledger_event("EX-1")),
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )
    assert len(result) == 1
    assert result[0].state is OrderLinkReviewState.CANDIDATE
    assert result[0].candidate_record_ids == ("LOG-1", "LOG-2")
    assert result[0].trade_ref == "42"


def test_three_source_chain_rejects_competing_observed_order() -> None:
    result = review_bot_execution_ledger_candidates(
        (
            bot_snapshot("42", positive=("ORDER",)),
            bot_snapshot("43", unknown=("ORDER",)),
        ),
        (fills(fill()),),
        ledger(ledger_event()),
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )
    assert result[0].state is OrderLinkReviewState.AMBIGUOUS
    assert result[0].candidate_record_ids == ()
    assert result[1].state is OrderLinkReviewState.NO_CANDIDATE


def test_three_source_chain_cannot_promote_incomplete_positive_order_coverage() -> None:
    result = review_bot_execution_ledger_candidates(
        (bot_snapshot("42", positive=("ORDER", "MISSING-ORDER")),),
        (fills(fill()),),
        ledger(ledger_event()),
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )[0]
    assert result.state is OrderLinkReviewState.AMBIGUOUS
    assert result.candidate_record_ids == ()


def test_three_source_chain_cannot_promote_partial_fee_ledger() -> None:
    result = review_bot_execution_ledger_candidates(
        (bot_snapshot("42", positive=("ORDER",)),),
        (fills(fill("EX-1"), fill("EX-2")),),
        ledger(ledger_event("EX-1")),
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )[0]
    assert result.state is OrderLinkReviewState.AMBIGUOUS
    assert result.candidate_record_ids == ()


def test_three_source_chain_rejects_unmapped_canonical_instrument() -> None:
    result = review_bot_execution_ledger_candidates(
        (bot_snapshot("42", positive=("ORDER",), instrument="BTC/USD:USD"),),
        (fills(fill()),),
        ledger(ledger_event()),
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )[0]
    assert result.state is OrderLinkReviewState.AMBIGUOUS


def test_three_source_chain_never_uses_unfilled_order() -> None:
    result = review_bot_execution_ledger_candidates(
        (bot_snapshot("42", zero=("ORDER",)),),
        (fills(fill()),),
        ledger(ledger_event()),
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )[0]
    assert result.state is OrderLinkReviewState.NO_CANDIDATE
    assert result.candidate_record_ids == ()


def test_three_source_chain_input_order_is_deterministic() -> None:
    bots = (
        bot_snapshot("43", positive=("ORDER",)),
        bot_snapshot("42", positive=("OTHER",)),
    )
    windows = (fills(fill("EX-1")),)
    log = ledger(ledger_event())
    actual = review_bot_execution_ledger_candidates(
        bots,
        windows,
        log,
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )
    expected = review_bot_execution_ledger_candidates(
        tuple(reversed(bots)),
        tuple(reversed(windows)),
        log,
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )
    assert actual == expected
