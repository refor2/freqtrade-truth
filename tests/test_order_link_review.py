"""Synthetic, read-only order-link candidate review for #15 (never verified ownership)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from freqtrade_truth.adapters.freqtrade_order_evidence import FreqtradeClosedTradeOrderEvidence
from freqtrade_truth.adapters.order_link_review import (
    OrderLinkReviewState,
    review_order_id_candidates,
)
from freqtrade_truth.core import NormalizedTradeRecord, SourceKind


def bot_snapshot(
    trade_ref: str,
    *,
    positive: tuple[str, ...] = (),
    zero: tuple[str, ...] = (),
    unknown: tuple[str, ...] = (),
) -> FreqtradeClosedTradeOrderEvidence:
    return FreqtradeClosedTradeOrderEvidence(
        source_name="test-freqtrade",
        trade_ref=trade_ref,
        instrument="BTC/USD:BTC",
        observed_exchange_order_ids=tuple(sorted((*positive, *zero, *unknown))),
        positive_fill_exchange_order_ids=positive,
        zero_fill_exchange_order_ids=zero,
        unknown_fill_exchange_order_ids=unknown,
    )


def exchange_record(
    record_id: str,
    order_id: str,
    *,
    currency: str = "BTC",
    instrument: str = "BTC/USD:BTC",
) -> NormalizedTradeRecord:
    return NormalizedTradeRecord(
        record_id=record_id,
        source_kind=SourceKind.EXCHANGE,
        source_name="test-bybit",
        trade_ref=order_id,
        instrument=instrument,
        settlement_currency=currency,
        closed_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
        reported_net_pnl=Decimal("5"),
    )


def test_equal_time_and_pnl_without_order_id_are_not_evidence() -> None:
    results = review_order_id_candidates(
        (bot_snapshot("42", positive=("KNOWN",)),),
        (exchange_record("foreign", "OTHER"),),
        settlement_currency="BTC",
    )
    assert len(results) == 1
    assert results[0].state is OrderLinkReviewState.NO_CANDIDATE
    assert results[0].candidate_record_ids == ()


def test_order_id_overlap_is_only_candidate_not_verified_match() -> None:
    result = review_order_id_candidates(
        (bot_snapshot("42", positive=("EX-ENTRY", "EX-EXIT")),),
        (
            exchange_record("exchange-1", "EX-EXIT"),
            exchange_record("foreign", "FOREIGN"),
        ),
        settlement_currency="BTC",
    )[0]
    assert result.state is OrderLinkReviewState.CANDIDATE
    assert result.source_name == "test-freqtrade"
    assert result.trade_ref == "42"
    assert result.candidate_record_ids == ("exchange-1",)


def test_zero_filled_or_unknown_orders_do_not_establish_candidates() -> None:
    result = review_order_id_candidates(
        (bot_snapshot("42", zero=("EX-ZERO",), unknown=("EX-UNKNOWN",)),),
        (
            exchange_record("exchange-zero", "EX-ZERO"),
            exchange_record("exchange-unknown", "EX-UNKNOWN"),
        ),
        settlement_currency="BTC",
    )[0]
    assert result.state is OrderLinkReviewState.NO_CANDIDATE
    assert result.candidate_record_ids == ()


def test_currency_and_instrument_must_match_exactly() -> None:
    result = review_order_id_candidates(
        (bot_snapshot("42", positive=("EX-1",)),),
        (
            exchange_record("wrong-currency", "EX-1", currency="USD"),
            exchange_record("wrong-instrument", "EX-1", instrument="ETH/USD:ETH"),
        ),
        settlement_currency="BTC",
    )[0]
    assert result.state is OrderLinkReviewState.NO_CANDIDATE


def test_shared_order_id_across_bots_fails_closed_for_both() -> None:
    snapshots = (bot_snapshot("42", positive=("EX-1",)), bot_snapshot("43", positive=("EX-1",)))
    records = (exchange_record("exchange-1", "EX-1"),)
    forward = review_order_id_candidates(snapshots, records, settlement_currency="BTC")
    backward = review_order_id_candidates(
        tuple(reversed(snapshots)), records, settlement_currency="BTC"
    )
    assert forward == backward
    assert [item.trade_ref for item in forward] == ["42", "43"]
    assert all(item.state is OrderLinkReviewState.AMBIGUOUS for item in forward)
    assert all(item.candidate_record_ids == () for item in forward)


def test_one_order_id_with_multiple_exchange_records_is_ambiguous() -> None:
    result = review_order_id_candidates(
        (bot_snapshot("42", positive=("EX-1",)),),
        (exchange_record("one", "EX-1"), exchange_record("two", "EX-1")),
        settlement_currency="BTC",
    )[0]
    assert result.state is OrderLinkReviewState.AMBIGUOUS
    assert result.candidate_record_ids == ()


def test_input_order_does_not_change_provisional_results() -> None:
    bots = (bot_snapshot("1", positive=("A", "B")), bot_snapshot("2", positive=("C",)))
    records = (exchange_record("x", "B"), exchange_record("y", "C"))
    assert review_order_id_candidates(
        bots, records, settlement_currency="BTC"
    ) == review_order_id_candidates(
        tuple(reversed(bots)), tuple(reversed(records)), settlement_currency="BTC"
    )


def test_duplicate_trade_snapshot_or_exchange_record_fails_closed() -> None:
    bot = bot_snapshot("42", positive=("EX-1",))
    exchange = exchange_record("one", "EX-1")
    with pytest.raises(ValueError, match="duplicate"):
        review_order_id_candidates((bot, bot), (exchange,), settlement_currency="BTC")
    with pytest.raises(ValueError, match="duplicate"):
        review_order_id_candidates((bot,), (exchange, exchange), settlement_currency="BTC")


def test_invalid_evidence_and_wrong_source_fails_closed() -> None:
    bot = bot_snapshot("42", positive=("EX-1",))
    malformed = FreqtradeClosedTradeOrderEvidence(
        source_name=bot.source_name,
        trade_ref=bot.trade_ref,
        instrument=bot.instrument,
        observed_exchange_order_ids=(),
        positive_fill_exchange_order_ids=("EX-1",),
    )
    with pytest.raises(ValueError, match="observed"):
        review_order_id_candidates(
            (malformed,), (exchange_record("one", "EX-1"),), settlement_currency="BTC"
        )

    wrong_source = NormalizedTradeRecord(
        record_id="wrong",
        source_kind=SourceKind.FREQTRADE,
        source_name="synthetic-bot",
        trade_ref="EX-1",
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        closed_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="exchange"):
        review_order_id_candidates((bot,), (wrong_source,), settlement_currency="BTC")


@pytest.mark.parametrize("competing_fill", ["unknown", "zero"])
def test_competing_observed_order_id_never_yields_exclusive_candidate(
    competing_fill: str,
) -> None:
    competitor = (
        bot_snapshot("43", unknown=("EX-1",))
        if competing_fill == "unknown"
        else bot_snapshot("43", zero=("EX-1",))
    )
    results = review_order_id_candidates(
        (bot_snapshot("42", positive=("EX-1",)), competitor),
        (exchange_record("exchange-1", "EX-1"),),
        settlement_currency="BTC",
    )
    assert results[0].state is OrderLinkReviewState.AMBIGUOUS
    assert results[0].candidate_record_ids == ()


def test_blank_settlement_currency_is_rejected() -> None:
    with pytest.raises(ValueError, match="settlement_currency"):
        review_order_id_candidates((), (), settlement_currency="   ")
