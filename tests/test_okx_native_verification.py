"""Fail-closed synthetic source identity and native money regressions."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from freqtrade_truth.core.okx_native_verification import (
    PositionNativeResult,
    PositionNativeStatus,
    verify_okx_native_batch,
    verify_okx_position_native,
)


def sample(*, first: int = 1000, ctime: int = 900, posid: str = "p1") -> dict[str, Any]:
    inst = "SYNTHETIC-INST-XPERP"
    return {
        "status": "PASS",
        "read_only": True,
        "financial_truth": False,
        "linkage": "STRONG_EXCHANGE_ORDER_ID",
        "exchange_order_ids": ["order-in", "order-out"],
        "exchange_orders": [
            {"order_id": "order-in", "role": "ENTRY", "ft_order_side": "buy"},
            {"order_id": "order-out", "role": "EXIT", "ft_order_side": "sell"},
        ],
        "exchange_evidence": {
            "status": "PASS",
            "read_only": True,
            "identity_verified": True,
            "fill_truncated": False,
            "bill_truncated": False,
            "position_history_truncated": False,
            "inst_id": inst,
            "algo_order_links": [],
            "fills": [
                {
                    "ord_id": "order-in",
                    "trade_id": "fill-1",
                    "inst_id": inst,
                    "fill_time": str(first),
                },
                {
                    "ord_id": "order-out",
                    "trade_id": "fill-2",
                    "inst_id": inst,
                    "fill_time": str(first + 200),
                },
            ],
            "position_history": [
                {
                    "inst_id": inst,
                    "type": "2",
                    "direction": "long",
                    "mgn_mode": "isolated",
                    "c_time": str(ctime),
                    "u_time": str(first + 250),
                    "pos_id": posid,
                    "ccy": "USDC",
                    "settled_pnl": "",
                    "pnl": 3.4,
                    "fee": -0.2,
                    "funding_fee": -0.1,
                    "liq_penalty": 0.0,
                    "realized_pnl": 3.1,
                },
            ],
        },
    }


def check(p: dict[str, Any]) -> PositionNativeResult:
    return verify_okx_position_native(p, side="long", tolerance=Decimal("0.00000001"))


def test_verified_source_cycle_yields_native_only_and_no_usd_claim() -> None:
    result = check(sample())
    assert result.status is PositionNativeStatus.CONSISTENT
    assert result.currency == "USDC"
    assert result.difference == 0
    assert result.usd_verified is False
    assert result.account_verified_by_library is False
    assert result.cycle_key == ("SYNTHETIC-INST-XPERP", "p1", 900, 1250)


def test_account_claim_without_verified_source_is_unknown() -> None:
    for mutation in (
        {"status": "PARTIAL"},
        {"financial_truth": True},
        {"linkage": "TIME_ONLY"},
        {"read_only": False},
    ):
        p = sample()
        p.update(mutation)
        assert check(p).status is PositionNativeStatus.UNKNOWN


@pytest.mark.parametrize(
    "key",
    (
        "identity_verified",
        "read_only",
        "fill_truncated",
        "bill_truncated",
        "position_history_truncated",
    ),
)
def test_unqualified_source_fails_closed(key: str) -> None:
    p = sample()
    p["exchange_evidence"][key] = not p["exchange_evidence"][key]
    assert check(p).status is PositionNativeStatus.UNKNOWN


def test_subsecond_candidate_is_not_fuzzily_assigned() -> None:
    p = sample(first=1000, ctime=1001)
    assert check(p).reason == "POSITION_CYCLE_NOT_FOUND"


def test_cross_trade_unlinked_fill_fails_closed() -> None:
    p = sample()
    p["exchange_evidence"]["fills"][0]["ord_id"] = "some-foreign-order"
    assert check(p).status is PositionNativeStatus.UNKNOWN


def test_missing_exit_fill_fails_closed() -> None:
    p = sample()
    p["exchange_evidence"]["fills"].pop()
    assert check(p).reason == "EXPECTED_ORDER_FILL_MISSING"


def test_duplicate_fill_trades_fail_closed() -> None:
    p = sample()
    p["exchange_evidence"]["fills"][1]["trade_id"] = "fill-1"
    assert check(p).reason == "DUPLICATE_EXCHANGE_FILL"


def test_missing_or_wrong_order_role_fails_closed() -> None:
    p = sample()
    p["exchange_orders"].pop()
    assert check(p).reason == "ORDER_ROLE_INCOMPLETE"
    p = sample()
    p["exchange_orders"][0]["role"] = "UNSUPPORTED"
    assert check(p).reason == "ORDER_ROLE_INVALID"


def test_extra_ambiguous_history_fails_closed() -> None:
    p = sample()
    p["exchange_evidence"]["position_history"].append(
        deepcopy(p["exchange_evidence"]["position_history"][0])
    )
    assert check(p).reason == "POSITION_CYCLE_AMBIGUOUS"


def test_nonzero_cross_settlement_and_missing_component_unknown() -> None:
    p = sample()
    p["exchange_evidence"]["position_history"][0]["settled_pnl"] = "1.00"
    assert check(p).reason == "UNSUPPORTED_NONZERO_SETTLED_PNL"
    p = sample()
    p["exchange_evidence"]["position_history"][0]["fee"] = None
    assert check(p).reason == "POSITION_NATIVE_COMPONENT_UNKNOWN"


def test_signed_native_arithmetic_difference_visible_not_pretend_match() -> None:
    p = sample()
    p["exchange_evidence"]["position_history"][0]["realized_pnl"] = 4.2
    assert check(p).status is PositionNativeStatus.DIFFERENT


def test_duplicate_position_cycle_conflicts_not_silent_double_count() -> None:
    p = sample()
    out = verify_okx_native_batch(
        [(p, "long"), (deepcopy(p), "long")], tolerance=Decimal("0.00000001")
    )
    assert [r.status for r in out] == [PositionNativeStatus.CONFLICT] * 2


def test_posid_reused_across_disjoint_cycles_is_not_collision() -> None:
    old = sample()
    later = sample(first=5000, ctime=4900, posid="p1")
    out = verify_okx_native_batch([(old, "long"), (later, "long")], tolerance=Decimal("0.00000001"))
    assert [r.status for r in out] == [PositionNativeStatus.CONSISTENT] * 2


def test_stoploss_triggered_child_only_through_strong_source_link() -> None:
    p = sample()
    p["exchange_orders"][1]["ft_order_side"] = "stoploss"
    p["exchange_evidence"]["algo_order_links"] = [
        {
            "source_order_id": "order-out",
            "source_type": "ALGO_CONDITIONAL",
            "state": "effective",
            "inst_id": "SYNTHETIC-INST-XPERP",
            "triggered_order_ids": ["trigger-child"],
        }
    ]
    p["exchange_evidence"]["fills"][1]["ord_id"] = "trigger-child"
    assert check(p).status is PositionNativeStatus.CONSISTENT
    p["exchange_evidence"]["algo_order_links"][0]["state"] = "UNKNOWN"
    assert check(p).status is PositionNativeStatus.UNKNOWN


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), None, "N/A"])
def test_invalid_native_values_remain_unknown(value: object) -> None:
    p = sample()
    p["exchange_evidence"]["position_history"][0]["pnl"] = value
    assert check(p).status is PositionNativeStatus.UNKNOWN


def test_invalid_tolerance_rejected() -> None:
    with pytest.raises(ValueError):
        verify_okx_position_native(sample(), side="long", tolerance=Decimal("-1"))


def test_ledger_usd_ownership_not_inferred_even_after_identity_match() -> None:
    p = sample()
    p["exchange_evidence"]["unlinked_instrument_bills"] = [
        {"pnl": "999", "ccy": "USDC", "ord_id": "some-other-trade"}
    ]
    result = check(p)
    assert result.status is PositionNativeStatus.CONSISTENT
    assert result.usd_verified is False
    assert result.account_verified_by_library is False


def test_native_summary_redacts_ids_and_never_promotes_usd() -> None:
    import json

    from freqtrade_truth.api.native_report import native_source_report

    data = {
        "schema": "freqtrade-truth.okx-native.v1",
        "entries": [{"side": "long", "source_payload": sample()}],
    }
    result = native_source_report(data, tolerance=Decimal("0.00000001"))
    assert result["states"] == {"consistent": 1, "different": 0, "unknown": 0, "conflict": 0}
    assert result["usd_verified"] is False
    assert result["account_verified_by_library"] is False
    rendered = json.dumps(result)
    assert "order-in" not in rendered
    assert "fill-1" not in rendered
    assert "SYNTHETIC-INST-XPERP" not in rendered


def test_native_report_cli_on_local_sanitized_source(tmp_path: object) -> None:
    import io
    import json
    from pathlib import Path

    from freqtrade_truth.cli import main

    file = Path(str(tmp_path)) / "source.json"
    file.write_text(
        json.dumps(
            {
                "schema": "freqtrade-truth.okx-native.v1",
                "entries": [{"side": "long", "source_payload": sample()}],
            }
        )
    )
    out, err = io.StringIO(), io.StringIO()
    rc = main(["okx-native-report", "--input", str(file)], stdout=out, stderr=err)
    assert rc == 0 and not err.getvalue()
    assert json.loads(out.getvalue())["states"]["consistent"] == 1
    assert "order-in" not in out.getvalue()
    # Malformed source must fail without echoing private content.
    file.write_text('{"secret":"VERY-SENSITIVE-ACCOUNT-ID"}')
    out, err = io.StringIO(), io.StringIO()
    assert main(["okx-native-report", "--input", str(file)], stdout=out, stderr=err) == 2
    assert "VERY-SENSITIVE" not in err.getvalue()


def test_native_report_rejects_source_metadata_pretending_to_be_account_proof() -> None:
    from freqtrade_truth.api.native_report import native_source_report

    with pytest.raises(ValueError):
        native_source_report(
            {
                "schema": "freqtrade-truth.okx-native.v1",
                "entries": [{"side": "long", "source_payload": sample(), "account_verified": True}],
            },
            tolerance=Decimal("0.00000001"),
        )


def fee_sample(*, first: int = 1000, ctime: int = 900, posid: str = "p1") -> dict[str, Any]:
    """Complete, invented matching native fee evidence for positive tests."""
    p = sample(first=first, ctime=ctime, posid=posid)
    e = p["exchange_evidence"]
    for f in e["fills"]:
        f["fee_ccy"] = "USDC"
        f["fee"] = "-0.1"
    e["linked_bills"] = [
        {
            "bill_id": "bill-1",
            "ord_id": "order-in",
            "trade_id": "fill-1",
            "sub_type": "1",
            "inst_id": "SYNTHETIC-INST-XPERP",
            "ccy": "USDC",
            "fee": "-0.1",
        },
        {
            "bill_id": "bill-2",
            "ord_id": "order-out",
            "trade_id": "fill-2",
            "sub_type": "2",
            "inst_id": "SYNTHETIC-INST-XPERP",
            "ccy": "USDC",
            "fee": "-0.1",
        },
    ]
    return p


def test_fee_ledger_complete_exact_fill_ids_and_native_position_fee() -> None:
    from freqtrade_truth.core.okx_fee_ledger import FeeEvidenceStatus, verify_okx_trade_fee_bills

    result = verify_okx_trade_fee_bills(fee_sample(), side="long", tolerance=Decimal("0.00000001"))
    assert result.status is FeeEvidenceStatus.MATCH
    assert result.reason == "COMPLETE_NATIVE_TRADE_FEE_EQUATION"
    assert (result.fill_count, result.matched_count, result.linked_bill_count) == (2, 2, 2)
    assert result.usd_verified is False
    assert result.account_verified_by_library is False


def test_fee_ledger_missing_positive_fee_is_incomplete_not_zero() -> None:
    from freqtrade_truth.core.okx_fee_ledger import FeeEvidenceStatus, verify_okx_trade_fee_bills

    p = fee_sample()
    p["exchange_evidence"]["linked_bills"].pop()
    result = verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))
    assert result.status is FeeEvidenceStatus.INCOMPLETE
    assert result.reason == "ONE_OR_MORE_FILL_BILLS_MISSING"
    assert result.matched_count == 1


def test_fee_ledger_differing_event_fee_is_not_a_match() -> None:
    from freqtrade_truth.core.okx_fee_ledger import FeeEvidenceStatus, verify_okx_trade_fee_bills

    p = fee_sample()
    p["exchange_evidence"]["linked_bills"][0]["fee"] = "-0.09"
    r = verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))
    assert r.status is FeeEvidenceStatus.DIFFERENT
    assert r.reason == "MATCHED_FILL_BILL_FEE_DIFFERENT"


def test_fee_ledger_position_fee_delta_is_not_a_match() -> None:
    from freqtrade_truth.core.okx_fee_ledger import FeeEvidenceStatus, verify_okx_trade_fee_bills

    p = fee_sample()
    p["exchange_evidence"]["position_history"][0]["fee"] = "-0.3"
    r = verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))
    assert r.status is FeeEvidenceStatus.DIFFERENT
    assert r.reason == "BILL_FEE_POSITION_FEE_DIFFERENT"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_bill_id",
        "wrong_currency",
        "foreign_fill_id",
        "duplicate_bill_id",
        "funding_subtype",
        "missing_fee",
    ],
)
def test_fee_ledger_bad_or_foreign_bill_is_not_attributed(mutation: str) -> None:
    from freqtrade_truth.core.okx_fee_ledger import FeeEvidenceStatus, verify_okx_trade_fee_bills

    p = fee_sample()
    bill = p["exchange_evidence"]["linked_bills"][1]
    if mutation == "missing_bill_id":
        bill["bill_id"] = ""
    elif mutation == "wrong_currency":
        bill["ccy"] = "USD"
    elif mutation == "foreign_fill_id":
        bill["trade_id"] = "another-position-fill"
    elif mutation == "duplicate_bill_id":
        bill["bill_id"] = "bill-1"
    elif mutation == "funding_subtype":
        bill["sub_type"] = "173"
    elif mutation == "missing_fee":
        bill["fee"] = None
    result = verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))
    assert result.status in (FeeEvidenceStatus.AMBIGUOUS, FeeEvidenceStatus.INCOMPLETE)


def test_fee_ledger_unverified_subsecond_position_remains_unverified() -> None:
    from freqtrade_truth.core.okx_fee_ledger import FeeEvidenceStatus, verify_okx_trade_fee_bills

    p = fee_sample(ctime=1001)
    r = verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))
    assert r.status is FeeEvidenceStatus.UNVERIFIED


def test_fee_ledger_unqualified_source_and_fx_do_not_pass() -> None:
    from freqtrade_truth.core.okx_fee_ledger import FeeEvidenceStatus, verify_okx_trade_fee_bills

    p = fee_sample()
    p["exchange_evidence"]["bill_truncated"] = True
    r = verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))
    assert r.status is FeeEvidenceStatus.UNVERIFIED
    p = fee_sample()
    p["exchange_evidence"]["fills"][0]["fee_ccy"] = "USD"
    r = verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))
    assert r.status is FeeEvidenceStatus.INCOMPLETE


def test_fee_ledger_duplicate_claim_across_distinct_cycles_is_conflict() -> None:
    from freqtrade_truth.core.okx_fee_ledger import (
        FeeEvidenceStatus,
        verify_okx_trade_fee_bill_batch,
    )

    one = fee_sample()
    another = fee_sample(first=5000, ctime=4900)
    result = verify_okx_trade_fee_bill_batch(
        [(one, "long"), (another, "long")], tolerance=Decimal("0.00000001")
    )
    assert all(r.status is FeeEvidenceStatus.CONFLICT for r in result)


def test_fee_ledger_safe_decimal_rejects_nan_inf_and_booleans() -> None:
    from freqtrade_truth.core.okx_fee_ledger import FeeEvidenceStatus, verify_okx_trade_fee_bills

    for fee in ("nan", "inf", True):
        p = fee_sample()
        p["exchange_evidence"]["linked_bills"][0]["fee"] = fee
        r = verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))
        assert r.status is FeeEvidenceStatus.INCOMPLETE


def test_fee_ledger_summary_only_redacts_ids_and_amounts() -> None:
    from freqtrade_truth.api.native_report import native_source_report

    report = native_source_report(
        {
            "schema": "freqtrade-truth.okx-native.v1",
            "entries": [{"side": "long", "source_payload": fee_sample()}],
        },
        tolerance=Decimal("0.00000001"),
    )
    fee_states = report["fee_ledger_states"]
    assert isinstance(fee_states, dict)
    assert fee_states["match"] == 1
    import json

    result = json.dumps(report)
    assert "bill-1" not in result and "order-in" not in result and "-0.1" not in result
    assert report["usd_verified"] is False


def test_fill_fee_position_match_without_complete_ledger() -> None:
    from freqtrade_truth.core.okx_fee_ledger import (
        FeeEvidenceStatus,
        verify_okx_fill_position_fees,
    )

    p = fee_sample()
    p["exchange_evidence"]["linked_bills"].pop()
    result = verify_okx_fill_position_fees(p, side="long", tolerance=Decimal("0.00000001"))
    assert result.status is FeeEvidenceStatus.MATCH
    assert result.reason == "FILL_FEE_POSITION_FEE_MATCH"
    from freqtrade_truth.core.okx_fee_ledger import verify_okx_trade_fee_bills

    ledger = verify_okx_trade_fee_bills(p, side="long", tolerance=Decimal("0.00000001"))
    assert ledger.status is FeeEvidenceStatus.INCOMPLETE


def test_fill_fee_position_mismatch_and_null_never_match() -> None:
    from freqtrade_truth.core.okx_fee_ledger import (
        FeeEvidenceStatus,
        verify_okx_fill_position_fees,
    )

    p = fee_sample()
    p["exchange_evidence"]["fills"][0]["fee"] = "-0.2"
    result = verify_okx_fill_position_fees(p, side="long", tolerance=Decimal("0.00000001"))
    assert result.status is FeeEvidenceStatus.DIFFERENT
    p["exchange_evidence"]["fills"][0]["fee"] = None
    result = verify_okx_fill_position_fees(p, side="long", tolerance=Decimal("0.00000001"))
    assert result.status is FeeEvidenceStatus.INCOMPLETE


def test_fill_fee_position_duplicate_claim_fails_closed() -> None:
    from freqtrade_truth.core.okx_fee_ledger import (
        FeeEvidenceStatus,
        verify_okx_fill_position_fee_batch,
    )

    p = fee_sample()
    results = verify_okx_fill_position_fee_batch(
        [(p, "long"), (deepcopy(p), "long")],
        tolerance=Decimal("0.00000001"),
    )
    assert all(result.status is FeeEvidenceStatus.CONFLICT for result in results)


def test_fill_fee_position_unknown_cycle_fails_closed() -> None:
    from freqtrade_truth.core.okx_fee_ledger import (
        FeeEvidenceStatus,
        verify_okx_fill_position_fees,
    )

    result = verify_okx_fill_position_fees(
        fee_sample(ctime=1001), side="long", tolerance=Decimal("0.00000001")
    )
    assert result.status is FeeEvidenceStatus.UNVERIFIED


def test_redacted_native_report_distinguishes_fill_fee_and_ledger_bill_coverage() -> None:
    from freqtrade_truth.api.native_report import native_source_report

    p = fee_sample()
    p["exchange_evidence"]["linked_bills"].pop()
    result = native_source_report(
        {
            "schema": "freqtrade-truth.okx-native.v1",
            "entries": [{"side": "long", "source_payload": p}],
        },
        tolerance=Decimal("0.00000001"),
    )
    fill_status = result["fee_fill_position_states"]
    bill_status = result["fee_ledger_states"]
    assert isinstance(fill_status, dict)
    assert isinstance(bill_status, dict)
    assert fill_status["match"] == 1
    assert bill_status["incomplete"] == 1
    assert result["usd_verified"] is False
