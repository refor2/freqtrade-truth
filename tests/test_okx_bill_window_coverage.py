"""No false full-ledger proof when the source queried the wrong time window."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from freqtrade_truth.core.okx_bill_window_coverage import (
    BillWindowStatus,
    verify_okx_bill_query_window,
    verify_okx_bill_query_window_batch,
)


def evidence() -> dict[str, Any]:
    return {
        "exchange_evidence": {
            "status": "PASS",
            "read_only": True,
            "trade_window": {"begin_ms": 1000, "end_ms": 2000},
            "fills": [
                {"ord_id": "in", "trade_id": "f1", "fill_time": "1200"},
                {"ord_id": "out", "trade_id": "f2", "fill_time": "2010"},
                {"ord_id": "out", "trade_id": "f3", "fill_time": "990"},
                {"ord_id": "out", "trade_id": "f4", "fill_time": "1800"},
            ],
            "linked_bills": [
                {"ord_id": "in", "trade_id": "f1", "bill_id": "b1"},
            ],
        }
    }


def test_missed_window_not_missing_exchange_bill() -> None:
    result = verify_okx_bill_query_window(evidence())
    assert result.status is BillWindowStatus.OUTSIDE_BILL_WINDOW
    assert result.reason == "BILL_QUERY_EXCLUDES_FILL_TIMESTAMPS"
    assert (
        result.linked_inside,
        result.unlinked_before,
        result.unlinked_inside,
        result.unlinked_after,
    ) == (1, 1, 1, 1)
    assert result.usd_verified is False
    assert result.account_ledger_verified is False


def test_inside_window_is_not_ledger_proof() -> None:
    p = evidence()
    p["exchange_evidence"]["trade_window"] = {"begin_ms": 900, "end_ms": 2100}
    r = verify_okx_bill_query_window(p)
    assert r.status is BillWindowStatus.COVERED_BY_WINDOW
    assert r.unlinked_inside == 3
    assert r.account_ledger_verified is False


def test_no_evidence_flags_or_missing_window_fail_closed() -> None:
    p = evidence()
    p["exchange_evidence"]["status"] = "PARTIAL"
    assert verify_okx_bill_query_window(p).status is BillWindowStatus.SOURCE_UNQUALIFIED
    p = evidence()
    del p["exchange_evidence"]["trade_window"]
    assert verify_okx_bill_query_window(p).status is BillWindowStatus.SOURCE_UNQUALIFIED


def test_linked_outside_window_is_self_contradiction() -> None:
    p = evidence()
    p["exchange_evidence"]["linked_bills"].append(
        {"ord_id": "out", "trade_id": "f2", "bill_id": "b2"}
    )
    assert verify_okx_bill_query_window(p).reason == "LINKED_FILL_OUTSIDE_BILL_WINDOW"


def test_duplicate_fill_and_foreign_bill_rejected() -> None:
    p = evidence()
    p["exchange_evidence"]["fills"].append(deepcopy(p["exchange_evidence"]["fills"][0]))
    assert verify_okx_bill_query_window(p).reason == "DUPLICATE_FILL_KEY"
    p = evidence()
    p["exchange_evidence"]["linked_bills"].append(
        {"ord_id": "foreign", "trade_id": "f9", "bill_id": "b9"}
    )
    assert verify_okx_bill_query_window(p).reason == "FOREIGN_LINKED_BILL"


def test_redacted_batch_aggregate_has_no_trade_ids() -> None:
    p = evidence()
    agg = verify_okx_bill_query_window_batch([(p, "long")])
    assert agg["fills"] == 4
    assert agg["unlinked_after_window"] == 1
    states = agg["states"]
    assert isinstance(states, dict)
    assert states["outside_bill_window"] == 1
    assert "f1" not in str(agg) and "f9" not in str(agg)
    assert agg["account_ledger_verified"] is False


def test_corrected_actual_bill_window_supersedes_old_operational_time_range() -> None:
    p = evidence()
    p["exchange_evidence"]["bill_query_window"] = {
        "begin_ms": 900,
        "end_ms": 2100,
    }
    r = verify_okx_bill_query_window(p)
    assert r.status is BillWindowStatus.COVERED_BY_WINDOW
    assert r.unlinked_inside == 3
    assert r.unlinked_after == 0
    assert r.unlinked_before == 0


def test_invalid_new_bill_window_does_not_fallback_to_old_window() -> None:
    p = evidence()
    p["exchange_evidence"]["bill_query_window"] = {"begin_ms": "invalid"}
    r = verify_okx_bill_query_window(p)
    assert r.status is BillWindowStatus.SOURCE_UNQUALIFIED
