"""Conservative fee-ledger candidate discovery, never final ownership or PnL.

Multiple TRADE fee events may belong to one partially filled order. Funding
SETTLEMENT events, even with the same order ID, must not be pulled into
trade fee candidates. This internal module never produces a fee total.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from freqtrade_truth.adapters.bybit_transaction_log import BybitTransactionLogEntry
from freqtrade_truth.adapters.freqtrade_order_evidence import FreqtradeClosedTradeOrderEvidence
from freqtrade_truth.adapters.order_link_review import (
    OrderLinkReview,
    OrderLinkReviewState,
    review_order_id_candidates,
)


def review_fee_ledger_order_candidates(
    bot_evidence: Sequence[FreqtradeClosedTradeOrderEvidence],
    ledger_entries: Sequence[BybitTransactionLogEntry],
    *,
    settlement_currency: str,
) -> tuple[OrderLinkReview, ...]:
    """Return only TRADE fee-event IDs whose orders have positive-fill evidence.

    Every result is **provisional**. Positive order linkage alone cannot prove
    account/position ownership, source completeness, fee attribution or native
    versus USD accounting. Funding is never attributed by this function.
    """
    # Reuse PR #22's strict bot-evidence and currency validator.
    review_order_id_candidates(bot_evidence, (), settlement_currency=settlement_currency)

    seen_ledger: set[str] = set()
    for event in ledger_entries:
        if not event.record_id.strip() or not event.source_name.strip():
            raise ValueError("ledger event identity must not be empty")
        if event.record_id in seen_ledger:
            raise ValueError("duplicate ledger event record_id")
        seen_ledger.add(event.record_id)
        for field in (event.trading_fees, event.funding):
            if field is not None and not field.is_finite():
                raise ValueError("ledger financial fields must be finite")

    observed_counts = Counter(
        order_id for snapshot in bot_evidence for order_id in snapshot.observed_exchange_order_ids
    )
    output: list[OrderLinkReview] = []

    for snapshot in sorted(bot_evidence, key=lambda item: (item.source_name, item.trade_ref)):
        observed = set(snapshot.observed_exchange_order_ids)
        positive = set(snapshot.positive_fill_exchange_order_ids)

        # Only explicit TRADE records are candidates. A SETTLEMENT entry may
        # expose orderId and even a fee field, but must never be attributed here.
        order_events = [
            event
            for event in ledger_entries
            if event.event_type == "TRADE" and event.order_ref in observed
        ]
        competing = any(
            observed_counts[event.order_ref] > 1
            for event in order_events
            if event.order_ref is not None
        )
        contradictory = any(
            event.instrument != snapshot.instrument
            or event.settlement_currency != settlement_currency
            or event.funding is not None
            or event.trading_fees is None
            for event in order_events
        )
        competing_sources = len({event.source_name for event in order_events}) > 1

        if competing or contradictory or competing_sources:
            state = OrderLinkReviewState.AMBIGUOUS
            ids: tuple[str, ...] = ()
        else:
            candidates = [event for event in order_events if event.order_ref in positive]
            state = (
                OrderLinkReviewState.CANDIDATE if candidates else OrderLinkReviewState.NO_CANDIDATE
            )
            ids = tuple(sorted(event.record_id for event in candidates))

        output.append(
            OrderLinkReview(
                source_name=snapshot.source_name,
                trade_ref=snapshot.trade_ref,
                state=state,
                candidate_record_ids=ids,
            )
        )
    return tuple(output)
