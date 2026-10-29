"""Reconcile exact source fill/account bill links independent of position clock."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from freqtrade_truth.core.okx_fee_ledger import (
    FeeEvidenceStatus,
    verify_okx_direct_fill_fee_ledger,
    verify_okx_direct_fill_fee_ledger_batch,
)
from freqtrade_truth.core.okx_native_verification import verify_okx_position_native

TOLERANCE = Decimal("0.00000001")


def _payload(*, cycle_clock_one_ms_late: bool = False) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "examples/okx-fee-evidence.synthetic.json"
    payload = cast(
        dict[str, Any], deepcopy(json.loads(path.read_text())["entries"][0]["source_payload"])
    )
    ev = payload["exchange_evidence"]
    for fill, bill in zip(ev["fills"], ev["linked_bills"], strict=True):
        fill["bill_id"] = bill["bill_id"]
    if cycle_clock_one_ms_late:
        first_fill = min(int(fill["fill_time"]) for fill in ev["fills"])
        ev["position_history"][0]["c_time"] = str(first_fill + 1)
    return payload


def test_one_ms_clock_issue_still_verifies_direct_trade_fee_ledger() -> None:
    p = _payload(cycle_clock_one_ms_late=True)
    assert verify_okx_position_native(p, side="long", tolerance=TOLERANCE).reason == (
        "POSITION_CYCLE_NOT_FOUND"
    )
    result = verify_okx_direct_fill_fee_ledger(p, side="long", tolerance=TOLERANCE)
    assert result.status is FeeEvidenceStatus.MATCH
    assert result.reason == "EXACT_FILL_ACCOUNT_BILL_LINK_AND_FEE"
    assert (result.fill_count, result.matched_count, result.linked_bill_count) == (2, 2, 2)
    assert result.currency == "USDC"
    assert result.usd_verified is False
    assert result.account_verified_by_library is False


def test_verified_position_cycle_is_unchanged() -> None:
    p = _payload()
    assert (
        verify_okx_direct_fill_fee_ledger(p, side="long", tolerance=TOLERANCE).status
        is FeeEvidenceStatus.MATCH
    )


@pytest.mark.parametrize(
    ("mutation", "status"),
    [
        ("missing_direct_bill_id", FeeEvidenceStatus.INCOMPLETE),
        ("wrong_direct_bill_id", FeeEvidenceStatus.AMBIGUOUS),
        ("wrong_linked_fee", FeeEvidenceStatus.DIFFERENT),
        ("missing_linked_bill", FeeEvidenceStatus.INCOMPLETE),
        ("unlinked_extra_bill", FeeEvidenceStatus.AMBIGUOUS),
        ("wrong_currency", FeeEvidenceStatus.INCOMPLETE),
        ("duplicate_direct_bill_id", FeeEvidenceStatus.AMBIGUOUS),
        ("invalid_fee", FeeEvidenceStatus.INCOMPLETE),
        ("source_truncated", FeeEvidenceStatus.UNVERIFIED),
        ("source_identity_missing", FeeEvidenceStatus.UNVERIFIED),
        ("untrusted_linkage", FeeEvidenceStatus.UNVERIFIED),
        ("missing_expected_order_fill", FeeEvidenceStatus.UNVERIFIED),
    ],
)
def test_wrong_untrusted_or_incomplete_fee_evidence_never_passes(
    mutation: str, status: FeeEvidenceStatus
) -> None:
    p = _payload(cycle_clock_one_ms_late=True)
    ev = p["exchange_evidence"]
    if mutation == "missing_direct_bill_id":
        del ev["fills"][1]["bill_id"]
    elif mutation == "wrong_direct_bill_id":
        ev["fills"][1]["bill_id"] = "foreign-bill"
    elif mutation == "wrong_linked_fee":
        ev["linked_bills"][1]["fee"] = "-0.001"
    elif mutation == "missing_linked_bill":
        ev["linked_bills"].pop()
    elif mutation == "unlinked_extra_bill":
        row = deepcopy(ev["linked_bills"][0])
        row.update(bill_id="other-bill", ord_id="other-order", trade_id="other-fill")
        ev["linked_bills"].append(row)
    elif mutation == "wrong_currency":
        ev["linked_bills"][0]["ccy"] = "USD"
    elif mutation == "duplicate_direct_bill_id":
        ev["fills"][1]["bill_id"] = ev["fills"][0]["bill_id"]
    elif mutation == "invalid_fee":
        ev["fills"][0]["fee"] = "nan"
    elif mutation == "source_truncated":
        ev["bill_truncated"] = True
    elif mutation == "source_identity_missing":
        ev["identity_verified"] = False
    elif mutation == "untrusted_linkage":
        p["linkage"] = "TIME_ONLY"
    elif mutation == "missing_expected_order_fill":
        ev["fills"].pop()
    assert verify_okx_direct_fill_fee_ledger(p, side="long", tolerance=TOLERANCE).status is status


def test_batch_rejects_shared_bill_claims() -> None:
    p = _payload(cycle_clock_one_ms_late=True)
    results = verify_okx_direct_fill_fee_ledger_batch(
        [(p, "long"), (deepcopy(p), "long")], tolerance=TOLERANCE
    )
    assert [r.status for r in results] == [FeeEvidenceStatus.CONFLICT] * 2


def test_report_exposes_separate_direct_fee_truth_even_when_native_unknown() -> None:
    from freqtrade_truth.api.native_report import native_source_report

    p = _payload(cycle_clock_one_ms_late=True)
    report = native_source_report(
        {
            "schema": "freqtrade-truth.okx-native.v1",
            "entries": [{"side": "long", "source_payload": p}],
        },
        tolerance=TOLERANCE,
    )
    states = report["states"]
    strict = report["fee_ledger_states"]
    direct = report["fee_direct_ledger_states"]
    assert isinstance(states, dict) and states["unknown"] == 1
    assert isinstance(strict, dict) and strict["unverified"] == 1
    assert isinstance(direct, dict) and direct["match"] == 1
    assert report["fee_direct_ledger_reason_counts"] == {"EXACT_FILL_ACCOUNT_BILL_LINK_AND_FEE": 1}
    assert report["usd_verified"] is False
    assert report["account_verified_by_library"] is False
