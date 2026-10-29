"""Sanitized local-only source-native report. No account fetches or mutation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from freqtrade_truth.core.okx_bill_window_coverage import verify_okx_bill_query_window_batch
from freqtrade_truth.core.okx_fee_ledger import (
    verify_okx_direct_fill_fee_ledger_batch,
    verify_okx_fill_position_fee_batch,
    verify_okx_trade_fee_bill_batch,
)
from freqtrade_truth.core.okx_funding_evidence import verify_okx_funding_candidates_batch
from freqtrade_truth.core.okx_native_verification import verify_okx_native_batch


def native_source_report(snapshot: object, *, tolerance: Decimal) -> dict[str, object]:
    """Read a bounded batch of sanitized OKX evidence, return counts only.

    This does not authenticate the source. The upstream caller must prove
    account scope. Claims in JSON files are not independent identity proof.
    """
    if not isinstance(snapshot, dict) or set(snapshot) != {"schema", "entries"}:
        raise ValueError("invalid native evidence envelope")
    if snapshot["schema"] != "freqtrade-truth.okx-native.v1":
        raise ValueError("unsupported native evidence schema")
    entries = snapshot["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError("native evidence must contain 1..1000 rows")
    parsed: list[tuple[Mapping[str, Any], str]] = []
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"side", "source_payload"}:
            raise ValueError("invalid native row")
        if item["side"] not in ("long", "short"):
            raise ValueError("native side must be long or short")
        if not isinstance(item["source_payload"], dict):
            raise ValueError("source_payload must be an object")
        parsed.append((item["source_payload"], item["side"]))
    result = verify_okx_native_batch(parsed, tolerance=tolerance)
    bill_window = verify_okx_bill_query_window_batch(parsed)
    fee_results = verify_okx_trade_fee_bill_batch(parsed, tolerance=tolerance)
    direct_fee_results = verify_okx_direct_fill_fee_ledger_batch(parsed, tolerance=tolerance)
    fill_fee_results = verify_okx_fill_position_fee_batch(parsed, tolerance=tolerance)
    funding_results = verify_okx_funding_candidates_batch(parsed, tolerance=tolerance)
    states = Counter(r.status.value for r in result)
    reasons = Counter(r.reason for r in result)
    fee_states = Counter(r.status.value for r in fee_results)
    fee_reasons = Counter(r.reason for r in fee_results)
    direct_fee_states = Counter(r.status.value for r in direct_fee_results)
    direct_fee_reasons = Counter(r.reason for r in direct_fee_results)
    fill_fee_states = Counter(r.status.value for r in fill_fee_results)
    fill_fee_reasons = Counter(r.reason for r in fill_fee_results)
    funding_states = Counter(r.status.value for r in funding_results)
    funding_reasons = Counter(r.reason for r in funding_results)
    return {
        "schema": "freqtrade-truth.okx-native-report.v1",
        "scope": "authenticated_adapter_required_for_account_proof",
        "total": len(result),
        "states": {s: states[s] for s in ("consistent", "different", "unknown", "conflict")},
        "reason_counts": dict(sorted(reasons.items())),
        "fee_ledger_states": {
            state: fee_states[state]
            for state in ("match", "different", "incomplete", "ambiguous", "unverified", "conflict")
        },
        "fee_ledger_reason_counts": dict(sorted(fee_reasons.items())),
        "fee_direct_ledger_states": {
            state: direct_fee_states[state]
            for state in ("match", "different", "incomplete", "ambiguous", "unverified", "conflict")
        },
        "fee_direct_ledger_reason_counts": dict(sorted(direct_fee_reasons.items())),
        "fee_fill_position_states": {
            state: fill_fee_states[state]
            for state in ("match", "different", "incomplete", "ambiguous", "unverified", "conflict")
        },
        "fee_fill_position_reason_counts": dict(sorted(fill_fee_reasons.items())),
        "funding_candidate_states": {
            state: funding_states[state]
            for state in (
                "candidate_sum_equal",
                "candidate_sum_different",
                "missing_source_event",
                "zero_source_not_proven",
                "unverified",
                "ambiguous",
                "source_conflict",
            )
        },
        "funding_candidate_reason_counts": dict(sorted(funding_reasons.items())),
        "funding_ownership_verified": False,
        "funding_ledger_coverage_verified": False,
        "bill_query_window_coverage": bill_window,
        "usd_verified": False,
        "account_verified_by_library": False,
        "read_only": True,
        "warning": (
            "Native arithmetic and fee-bill evidence are separate. "
            "Missing trade bills remain incomplete; funding is time-based "
            "candidate arithmetic only; none proves ownership, account or USD."
        ),
    }
