"""Redacted authenticated two-source OKX CLI has no trading surface."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping

import pytest

import freqtrade_truth.okx_candidate as candidate
from freqtrade_truth.cli import main


def test_okx_candidate_cli_suppresses_account_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(candidate_config: object, *, environ: Mapping[str, str]) -> dict[str, object]:
        assert candidate_config is not None
        return {
            "read_only": True,
            "candidate_numeric_equal": 1,
            "candidate_numeric_different": 0,
            "unverified_or_ambiguous_groups": 0,
            "verified_account_financial_truth": False,
            "warning": "unverified",
        }

    monkeypatch.setattr(candidate, "compare_okx_candidate", fake)
    out, err = io.StringIO(), io.StringIO()
    code = main(
        [
            "reconcile-okx",
            "--freqtrade-url",
            "http://127.0.0.1:8082",
            "--region",
            "eea",
            "--inst-id",
            "BTC-USD-SWAP",
            "--inst-type",
            "SWAP",
            "--settle",
            "BTC",
            "--pair",
            "BTC/USD:BTC",
            "--json",
        ],
        environ={},
        stdout=out,
        stderr=err,
    )
    assert code == 0
    assert err.getvalue() == ""
    result = json.loads(out.getvalue())
    assert result["candidate_numeric_equal"] == 1
    assert result["verified_account_financial_truth"] is False


def test_okx_candidate_cli_does_not_echo_credentials_on_failure() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        [
            "reconcile-okx",
            "--region",
            "eea",
            "--inst-id",
            "BTC-USD-SWAP",
            "--inst-type",
            "SWAP",
            "--settle",
            "BTC",
            "--pair",
            "BTC/USD:BTC",
        ],
        environ={"OKX_READ_API_KEY": "synthetic-secret-not-real"},
        stdout=out,
        stderr=err,
    )
    assert code == 2
    assert not out.getvalue()
    assert "synthetic-secret-not-real" not in err.getvalue()
