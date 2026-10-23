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
