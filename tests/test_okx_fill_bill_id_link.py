"""Optional direct OKX fill billId -> account billId guards (synthetic only)."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from freqtrade_truth.core.okx_fee_ledger import (
    FeeEvidenceResult,
    FeeEvidenceStatus,
    verify_okx_trade_fee_bills,
)


def _sample() -> dict[str, Any]:
    src = Path(__file__).resolve().parents[1] / "examples/okx-fee-evidence.synthetic.json"
    return deepcopy(json.loads(src.read_text())["entries"][0]["source_payload"])


def _verify(p: dict[str, Any]) -> FeeEvidenceResult:
    return verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))


def test_full_fill_bill_id_exact_pair_match() -> None:
    p = _sample()
    fills = p["exchange_evidence"]["fills"]
    fills[0]["bill_id"], fills[1]["bill_id"] = "bill-0-0", "bill-0-1"
    assert _verify(p).status is FeeEvidenceStatus.MATCH


@pytest.mark.parametrize("variant", ["swapped", "foreign"])
def test_contradictory_bill_ids_reject_even_equal_fees(variant: str) -> None:
    p = _sample()
    fills = p["exchange_evidence"]["fills"]
    fills[0]["bill_id"] = "bill-0-1" if variant == "swapped" else "foreign-bill"
    fills[1]["bill_id"] = "bill-0-0" if variant == "swapped" else "bill-0-1"
    r = _verify(p)
    assert (r.status, r.reason) == (FeeEvidenceStatus.AMBIGUOUS, "FILL_BILL_ID_DISAGREES")


@pytest.mark.parametrize("invalid", ["absent", "", 42])
def test_partial_bill_id_coverage_fails_closed(invalid: object) -> None:
    p = _sample()
    fills = p["exchange_evidence"]["fills"]
    fills[0]["bill_id"] = "bill-0-0"
    if invalid != "absent":
        fills[1]["bill_id"] = invalid
    r = _verify(p)
    assert (r.status, r.reason) == (
        FeeEvidenceStatus.INCOMPLETE,
        "FILL_BILL_ID_COVERAGE_INCOMPLETE",
    )


def test_legacy_sanitized_fills_without_bill_id_remain_supported() -> None:
    assert _verify(_sample()).status is FeeEvidenceStatus.MATCH
