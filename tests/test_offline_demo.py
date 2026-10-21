"""Reviewer-friendly synthetic financial preview: no network or credentials."""

import io
import json

import pytest

import freqtrade_truth.cli as cli


def test_offline_demo_json_includes_three_preview_outcomes_without_verified_match() -> None:
    out = io.StringIO()
    err = io.StringIO()
    code = cli.main(["demo", "--json"], stdout=out, stderr=err, environ={})
    assert code == 0
    assert err.getvalue() == ""
    result = json.loads(out.getvalue())
    assert result["scope"] == "synthetic_numeric_preview_only"
    assert result["verified_trade_identity"] is False
    assert result["uses_exchange_api"] is False
    assert result["status"] == "ok"
    assert [(x["name"], x["numeric_status"]) for x in result["scenarios"]] == [
        ("equal_amounts_not_verified_ownership", "match"),
        ("different_amounts", "review"),
        ("missing_exchange_amount", "incomplete"),
    ]
    assert result["scenarios"][0]["verified_trade_identity"] is False
    assert result["scenarios"][0]["difference"] == "0.00"
    assert result["scenarios"][1]["difference"] == "-0.20"
    assert result["scenarios"][2]["difference"] is None


def test_offline_demo_is_deterministic_and_human_output_warns_about_identity() -> None:
    a, b, human = io.StringIO(), io.StringIO(), io.StringIO()
    assert cli.main(["demo", "--json"], stdout=a, environ={}) == 0
    assert cli.main(["demo", "--json"], stdout=b, environ={}) == 0
    assert a.getvalue() == b.getvalue()
    assert cli.main(["demo"], stdout=human, environ={}) == 0
    assert "SYNTHETIC" in human.getvalue()
    assert "NOT verified ownership" in human.getvalue()
    assert "review" in human.getvalue()
    assert "incomplete" in human.getvalue()


def test_offline_demo_does_not_invoke_network_or_emit_passed_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline demo cannot call an exchange transport")

    monkeypatch.setattr(cli, "run_bybit_check", forbidden)
    out, err = io.StringIO(), io.StringIO()
    assert (
        cli.main(
            ["demo", "--json"],
            environ={
                "BYBIT_API_KEY": "unmistakable-secret-key",
                "BYBIT_API_SECRET": "unmistakable-secret-token",
            },
            stdout=out,
            stderr=err,
        )
        == 0
    )
    assert not err.getvalue()
    assert "unmistakable-secret-" not in out.getvalue()
