"""Provisional read-only order-ID candidate review for issue #15.

Exact order-ID overlap is *not* verified ownership: no account scope, lifecycle
completeness, fill attribution, or financial reconciliation is established here.
This module is deliberately not exported as a stable public v0.1 contract.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from freqtrade_truth.adapters.freqtrade_order_evidence import FreqtradeClosedTradeOrderEvidence
from freqtrade_truth.core.models import NormalizedTradeRecord, SourceKind


class OrderLinkReviewState(StrEnum):
    """Provisional candidate discovery only; never a final reconciliation status."""

    NO_CANDIDATE = "no_candidate"
    CANDIDATE = "candidate_only"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class OrderLinkReview:
    """One bot-side trade's candidate Bybit closed-PnL record references."""

    source_name: str
    trade_ref: str
    state: OrderLinkReviewState
    candidate_record_ids: tuple[str, ...]


def review_order_id_candidates(
    bot_evidence: Sequence[FreqtradeClosedTradeOrderEvidence],
    exchange_records: Sequence[NormalizedTradeRecord],
    *,
    settlement_currency: str,
) -> tuple[OrderLinkReview, ...]:
    """Find candidate records from *positive-fill* exact order-ID overlap.

    No reconciliation MATCH or ownership claim is made. All potentially
    competing bot snapshots and exchange records must be passed together;
    incomplete input cannot establish exclusivity or verified identity.
    """
    if not settlement_currency.strip() or settlement_currency != settlement_currency.upper():
        raise ValueError("settlement_currency must be non-empty uppercase")

    seen_bots: set[tuple[str, str]] = set()
    owner_counts: Counter[str] = Counter()
    for snapshot in bot_evidence:
        identity = (snapshot.source_name, snapshot.trade_ref)
        if not all(part.strip() for part in (*identity, snapshot.instrument)):
            raise ValueError("bot evidence identity must not be empty")
        if identity in seen_bots:
            raise ValueError("duplicate bot trade evidence snapshot")
        seen_bots.add(identity)

        observed = snapshot.observed_exchange_order_ids
        positive = snapshot.positive_fill_exchange_order_ids
        zero = snapshot.zero_fill_exchange_order_ids
        unknown = snapshot.unknown_fill_exchange_order_ids
        if (
            len(set(observed)) != len(observed)
            or not all(order_id.strip() for order_id in observed)
            or len(set((*positive, *zero, *unknown))) != len((*positive, *zero, *unknown))
            or not set((*positive, *zero, *unknown)).issubset(observed)
        ):
            raise ValueError("bot fill categories must be unique subsets of observed orders")
        # Even an unknown or zero-fill reference held by another bot makes
        # cross-trade order-ID ownership insufficiently exclusive.
        owner_counts.update(observed)

    seen_exchange: set[tuple[str, str]] = set()
    for record in exchange_records:
        if record.source_kind is not SourceKind.EXCHANGE:
            raise ValueError("closed-PnL inputs must be exchange records")
        identity = (record.source_name, record.record_id)
        if identity in seen_exchange:
            raise ValueError("duplicate exchange source record")
        seen_exchange.add(identity)

    results: list[OrderLinkReview] = []
    for snapshot in sorted(bot_evidence, key=lambda item: (item.source_name, item.trade_ref)):
        positive_ids = set(snapshot.positive_fill_exchange_order_ids)
        matches = [
            record
            for record in exchange_records
            if record.trade_ref in positive_ids
            and record.instrument == snapshot.instrument
            and record.settlement_currency == settlement_currency
        ]
        matching_order_ids = [record.trade_ref for record in matches]
        if any(owner_counts[order_id] > 1 for order_id in matching_order_ids) or (
            len(set(matching_order_ids)) != len(matching_order_ids)
        ):
            state = OrderLinkReviewState.AMBIGUOUS
            candidate_ids: tuple[str, ...] = ()
        elif matches:
            state = OrderLinkReviewState.CANDIDATE
            candidate_ids = tuple(sorted(record.record_id for record in matches))
        else:
            state = OrderLinkReviewState.NO_CANDIDATE
            candidate_ids = ()
        results.append(
            OrderLinkReview(
                source_name=snapshot.source_name,
                trade_ref=snapshot.trade_ref,
                state=state,
                candidate_record_ids=candidate_ids,
            )
        )
    return tuple(results)
