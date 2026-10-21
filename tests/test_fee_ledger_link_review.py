"""Synthetic fee-ledger candidate tests. No economic ownership or fee total inferred."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from freqtrade_truth.adapters.bybit_transaction_log import BybitTransactionLogEntry
from freqtrade_truth.adapters.fee_ledger_link_review import review_fee_ledger_order_candidates
from freqtrade_truth.adapters.freqtrade_order_evidence import FreqtradeClosedTradeOrderEvidence
from freqtrade_truth.adapters.order_link_review import OrderLinkReviewState

AT = datetime(2030, 1, 1, 12, tzinfo=UTC)


def bot(
    trade_ref: str,
    *,
    positive: tuple[str, ...] = (),
    unknown: tuple[str, ...] = (),
    zero: tuple[str, ...] = (),
) -> FreqtradeClosedTradeOrderEvidence:
    return FreqtradeClosedTradeOrderEvidence(
        source_name="synthetic-bot",
        trade_ref=trade_ref,
        instrument="BTC/USD:BTC",
        observed_exchange_order_ids=tuple(sorted((*positive, *unknown, *zero))),
        positive_fill_exchange_order_ids=positive,
        unknown_fill_exchange_order_ids=unknown,
        zero_fill_exchange_order_ids=zero,
    )


def event(
    id: str,
    *,
    order_id: str | None = "A",
    event_type: str = "TRADE",
    instrument: str | None = "BTC/USD:BTC",
    currency: str = "BTC",
    fees: Decimal | None = Decimal("-0.01"),
    funding: Decimal | None = None,
    source_name: str = "synthetic-bybit",
) -> BybitTransactionLogEntry:
    return BybitTransactionLogEntry(
        record_id=f"bybit-ledger:{id}",
        event_ref=id,
        source_name=source_name,
        event_type=event_type,
        instrument=instrument,
        settlement_currency=currency,
        occurred_at=AT,
        funding=funding,
        trading_fees=fees,
        cash_flow=Decimal("0"),
        reported_net_change=Decimal("0"),
        order_ref=order_id,
        trade_ref=id,
    )


def test_two_partial_fill_fees_and_a_rebate_are_candidates_not_an_aggregated_total() -> None:
    """One order can legitimately have N distinct fee ledger events."""
    result = review_fee_ledger_order_candidates(
        (bot("42", positive=("A", "B")),),
        (
            event("entry-2", order_id="A", fees=Decimal("-0.02")),
            event("entry-1", order_id="A", fees=Decimal("-0.01")),
            event("exit-rebate", order_id="B", fees=Decimal("0.004")),
        ),
        settlement_currency="BTC",
    )
    assert len(result) == 1
    assert result[0].state is OrderLinkReviewState.CANDIDATE
    assert result[0].candidate_record_ids == (
        "bybit-ledger:entry-1",
        "bybit-ledger:entry-2",
        "bybit-ledger:exit-rebate",
    )
    assert not hasattr(result[0], "trading_fees")
    assert not hasattr(result[0], "funding")


def test_funding_settlement_with_the_same_order_id_is_not_a_fee_candidate() -> None:
    settlement = event(
        "funding",
        order_id="A",
        event_type="SETTLEMENT",
        fees=Decimal("0"),
        funding=Decimal("-0.001"),
    )
    no_trade = review_fee_ledger_order_candidates(
        (bot("42", positive=("A",)),), (settlement,), settlement_currency="BTC"
    )[0]
    assert no_trade.state is OrderLinkReviewState.NO_CANDIDATE
    assert no_trade.candidate_record_ids == ()

    trade_and_funding = review_fee_ledger_order_candidates(
        (bot("42", positive=("A",)),),
        (settlement, event("trade", order_id="A")),
        settlement_currency="BTC",
    )[0]
    assert trade_and_funding.state is OrderLinkReviewState.CANDIDATE
    assert trade_and_funding.candidate_record_ids == ("bybit-ledger:trade",)


@pytest.mark.parametrize(
    "kind",
    ["SETTLEMENT", "TRANSFER", "INTERNAL", "", "Trade"],
)
def test_non_trade_ledger_event_with_fee_is_not_considered_trade_fee(kind: str) -> None:
    result = review_fee_ledger_order_candidates(
        (bot("42", positive=("A",)),),
        (event("other", event_type=kind),),
        settlement_currency="BTC",
    )[0]
    assert result.state is OrderLinkReviewState.NO_CANDIDATE


def test_missing_order_link_cannot_be_inferred_from_symbol_time_or_pnl() -> None:
    result = review_fee_ledger_order_candidates(
        (bot("42", positive=("A",)),),
        (event("unlinked", order_id=None),),
        settlement_currency="BTC",
    )[0]
    assert result.state is OrderLinkReviewState.NO_CANDIDATE
    assert result.candidate_record_ids == ()


def test_unknown_and_zero_filled_order_refs_are_not_fee_candidates() -> None:
    result = review_fee_ledger_order_candidates(
        (bot("42", zero=("A",), unknown=("B",)),),
        (event("a", order_id="A"), event("b", order_id="B")),
        settlement_currency="BTC",
    )[0]
    assert result.state is OrderLinkReviewState.NO_CANDIDATE


def test_competing_observed_order_refs_fail_closed_for_both_bots() -> None:
    results = review_fee_ledger_order_candidates(
        (bot("42", positive=("A",)), bot("43", unknown=("A",))),
        (event("only-trade", order_id="A"),),
        settlement_currency="BTC",
    )
    assert tuple(x.trade_ref for x in results) == ("42", "43")
    assert all(x.state is OrderLinkReviewState.AMBIGUOUS for x in results)
    assert all(x.candidate_record_ids == () for x in results)


def test_contradictory_currency_instrument_or_source_for_order_is_ambiguous() -> None:
    source = bot("42", positive=("A",))
    for foreign in (
        event("wrong-currency", currency="USD"),
        event("wrong-instrument", instrument="ETH/USD:ETH"),
        event("unknown-instrument", instrument=None),
    ):
        result = review_fee_ledger_order_candidates(
            (source,), (event("correct"), foreign), settlement_currency="BTC"
        )[0]
        assert result.state is OrderLinkReviewState.AMBIGUOUS
        assert result.candidate_record_ids == ()


def test_trade_ledger_with_mixed_funding_and_fee_is_ambiguous() -> None:
    result = review_fee_ledger_order_candidates(
        (bot("42", positive=("A",)),),
        (event("mixed", funding=Decimal("-0.002")),),
        settlement_currency="BTC",
    )[0]
    assert result.state is OrderLinkReviewState.AMBIGUOUS
    assert result.candidate_record_ids == ()


def test_both_input_orders_have_identical_deterministic_result() -> None:
    bots = (bot("43", positive=("B",)), bot("42", positive=("A",)))
    ledger = (event("b2", order_id="B"), event("a1", order_id="A"), event("b1", order_id="B"))
    expected = review_fee_ledger_order_candidates(bots, ledger, settlement_currency="BTC")
    assert expected == review_fee_ledger_order_candidates(
        tuple(reversed(bots)), tuple(reversed(ledger)), settlement_currency="BTC"
    )


def test_duplicate_ledger_record_fails_closed_no_double_consumption() -> None:
    entry = event("duplicated")
    with pytest.raises(ValueError, match="duplicate"):
        review_fee_ledger_order_candidates(
            (bot("42", positive=("A",)),), (entry, entry), settlement_currency="BTC"
        )


@pytest.mark.parametrize("currency", ["", "  ", "btc"])
def test_invalid_currency_rejected(currency: str) -> None:
    with pytest.raises(ValueError, match="settlement_currency"):
        review_fee_ledger_order_candidates((), (), settlement_currency=currency)


def test_inconsistent_bot_order_evidence_rejected_by_existing_validator() -> None:
    broken = FreqtradeClosedTradeOrderEvidence(
        source_name="bot",
        trade_ref="42",
        instrument="BTC/USD:BTC",
        observed_exchange_order_ids=(),
        positive_fill_exchange_order_ids=("A",),
    )
    with pytest.raises(ValueError, match="observed"):
        review_fee_ledger_order_candidates((broken,), (), settlement_currency="BTC")


def test_explicit_zero_funding_on_trade_does_not_hide_fee_candidate() -> None:
    """A source-observed zero funding amount is not a mixed funding charge."""
    result = review_fee_ledger_order_candidates(
        (bot("42", positive=("A",)),),
        (event("trade-zero-funding", order_id="A", funding=Decimal("0")),),
        settlement_currency="BTC",
    )[0]
    assert result.state is OrderLinkReviewState.CANDIDATE
    assert result.candidate_record_ids == ("bybit-ledger:trade-zero-funding",)
