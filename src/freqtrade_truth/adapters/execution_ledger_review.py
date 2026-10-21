"""Candidate-only Bybit execId / transaction-log tradeId corroboration.

Even corroborated exchange records do NOT prove Freqtrade bot, position,
account, lifecycle coverage or complete financial attribution.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from freqtrade_truth.adapters.bybit_execution_evidence import BybitExecutionWindowEvidence
from freqtrade_truth.adapters.bybit_transaction_log import (
    BybitPaginatedWindowEvidence,
    BybitTransactionLogEntry,
)
from freqtrade_truth.adapters.freqtrade_order_evidence import FreqtradeClosedTradeOrderEvidence
from freqtrade_truth.adapters.order_link_review import (
    OrderLinkReview,
    OrderLinkReviewState,
    review_order_id_candidates,
)


def review_execution_ledger_candidates(
    windows: Sequence[BybitExecutionWindowEvidence],
    ledger: BybitPaginatedWindowEvidence,
) -> tuple[OrderLinkReview, ...]:
    """Corroborate exact execution, order, currency and fee, never claim ownership.

    Caller must supply all relevant source windows, but page exhaustion alone
    does not establish historical retention or completeness of the lifecycle.
    """
    if not ledger.pagination_exhausted:
        raise ValueError("ledger coverage is not pagination-exhausted")

    entries_by_trade: dict[str, BybitTransactionLogEntry] = {}
    ledger_ids: set[str] = set()
    for source_entry in ledger.records:
        if source_entry.record_id in ledger_ids or not source_entry.record_id.strip():
            raise ValueError("duplicate or missing ledger record ID")
        ledger_ids.add(source_entry.record_id)
        if not ledger.start_at <= source_entry.occurred_at <= ledger.end_at:
            raise ValueError("ledger coverage does not contain event timestamps")
        if source_entry.event_type != "TRADE" or source_entry.trade_ref is None:
            continue
        if source_entry.trade_ref in entries_by_trade:
            raise ValueError("duplicate ledger TRADE tradeId")
        entries_by_trade[source_entry.trade_ref] = source_entry

    observed_executions: set[tuple[str, str]] = set()
    results: list[OrderLinkReview] = []

    for window in windows:
        if (
            not window.pagination_exhausted
            or window.start_at < ledger.start_at
            or window.end_at > ledger.end_at
            or window.source_name != ledger.source_name
            or window.symbol != ledger.native_symbol
            or window.settlement_currency != ledger.settlement_currency
        ):
            raise ValueError("source coverage, symbol or currency mismatch")

        for execution in window.records:
            key = (window.source_name, execution.execution_ref)
            if key in observed_executions or not execution.execution_ref.strip():
                raise ValueError("duplicate execution reference")
            observed_executions.add(key)
            if (
                execution.order_ref != window.order_ref
                or execution.symbol != window.symbol
                or not window.start_at <= execution.occurred_at <= window.end_at
            ):
                raise ValueError("execution coverage or order identity mismatch")

            event = entries_by_trade.get(execution.execution_ref)
            if event is None:
                state = OrderLinkReviewState.NO_CANDIDATE
                ids: tuple[str, ...] = ()
            elif (
                event.source_name != window.source_name
                or event.order_ref != execution.order_ref
                or event.instrument != window.symbol
                or event.settlement_currency != window.settlement_currency
                or execution.fee_currency is None
                or execution.fee_currency != event.settlement_currency
                or execution.source_exec_fee is None
                or event.trading_fees is None
                or event.funding not in (None, 0)
                or execution.source_exec_fee != -event.trading_fees
            ):
                state = OrderLinkReviewState.AMBIGUOUS
                ids = ()
            else:
                state = OrderLinkReviewState.CANDIDATE
                ids = (event.record_id,)

            results.append(
                OrderLinkReview(
                    source_name=window.source_name,
                    trade_ref=execution.execution_ref,
                    state=state,
                    candidate_record_ids=ids,
                )
            )
    return tuple(sorted(results, key=lambda r: (r.source_name, r.trade_ref)))


def review_bot_execution_ledger_candidates(
    bot_evidence: Sequence[FreqtradeClosedTradeOrderEvidence],
    execution_windows: Sequence[BybitExecutionWindowEvidence],
    ledger_window: BybitPaginatedWindowEvidence,
    *,
    settlement_currency: str,
    normalized_instrument: str,
) -> tuple[OrderLinkReview, ...]:
    """Provisional three-source candidate discovery; never a verified trade.

    Stronger than a timestamp join: the chain requires Freqtrade positive-filled
    observed order IDs, exact Bybit execution IDs and matching transaction-log
    trade IDs, explicit instrument mapping, and source fee corroboration.

    It still cannot establish exchange account/bot exclusivity, complete order
    history, position lifecycle or financial reconciliation.
    """
    if not normalized_instrument.strip():
        raise ValueError("normalized_instrument is required")
    # Reuse strict snapshot validation, including fill partitions and identity.
    review_order_id_candidates(bot_evidence, (), settlement_currency=settlement_currency)
    ledger_links = review_execution_ledger_candidates(execution_windows, ledger_window)
    by_exec = {link.trade_ref: link for link in ledger_links}

    windows_by_order: dict[str, BybitExecutionWindowEvidence] = {}
    for window in execution_windows:
        if window.order_ref in windows_by_order:
            raise ValueError("duplicate order execution window")
        windows_by_order[window.order_ref] = window

    observed_counts = Counter(
        order_id for snapshot in bot_evidence for order_id in snapshot.observed_exchange_order_ids
    )
    results: list[OrderLinkReview] = []

    for snapshot in sorted(bot_evidence, key=lambda r: (r.source_name, r.trade_ref)):
        positive_ids = snapshot.positive_fill_exchange_order_ids
        if not positive_ids:
            state = OrderLinkReviewState.NO_CANDIDATE
            candidates: tuple[str, ...] = ()
        elif (
            snapshot.instrument != normalized_instrument
            or any(observed_counts[order_id] > 1 for order_id in positive_ids)
            or any(order_id not in windows_by_order for order_id in positive_ids)
        ):
            state = OrderLinkReviewState.AMBIGUOUS
            candidates = ()
        else:
            order_windows = [windows_by_order[order_id] for order_id in positive_ids]
            collected = [
                by_exec.get(execution.execution_ref)
                for window in order_windows
                for execution in window.records
            ]
            if not collected or any(
                link is None or link.state is not OrderLinkReviewState.CANDIDATE
                for link in collected
            ):
                state = OrderLinkReviewState.AMBIGUOUS
                candidates = ()
            else:
                state = OrderLinkReviewState.CANDIDATE
                candidates = tuple(
                    sorted(
                        id
                        for link in collected
                        if link is not None
                        for id in link.candidate_record_ids
                    )
                )

        results.append(
            OrderLinkReview(
                source_name=snapshot.source_name,
                trade_ref=snapshot.trade_ref,
                state=state,
                candidate_record_ids=candidates,
            )
        )
    return tuple(results)
