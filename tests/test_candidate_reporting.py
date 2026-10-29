"""Offline reporting acceptance tests for the existing Freqtrade Truth core."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from freqtrade_truth.api.http import make_application
from freqtrade_truth.api.reporting import as_csv, as_html, as_json, build_report
from freqtrade_truth.api.snapshot import parse_snapshot
from freqtrade_truth.cli import main


def fixture(
    bot_amount: str = "12.34",
    exchange_amount: str | None = "12.34",
    *,
    exchange_currency: str = "USDC",
) -> dict[str, Any]:
    def row(name: str, amount: str | None, currency: str) -> dict[str, object]:
        return {
            "record_id": "PRIVATE-ID-" + name,
            "source_name": "synthetic-" + name,
            "trade_ref": "SECRET-TRADE-ID-" + name,
            "instrument": "BTC/USD:USD-310404",
            "settlement_currency": currency,
            "closed_at": "2026-10-22T00:00:00Z",
            "reported_net_pnl": amount,
            "price_pnl": None,
            "trading_fees": None,
            "funding": None,
            "other_adjustments": None,
        }

    return {
        "schema": "freqtrade-truth.candidate.v1",
        "freqtrade": [row("bot", bot_amount, "USDC")],
        "exchange": [row("exchange", exchange_amount, exchange_currency)],
        "tolerance": {"close_seconds": 0, "reported_net_pnl": "0.01"},
    }


def report(snapshot: dict[str, Any], *, values: bool = False) -> dict[str, Any]:
    bot, exchange, tol = parse_snapshot(snapshot)
    return build_report(bot, exchange, tolerance=tol, include_values=values)


def test_numeric_match_always_unverified_and_redacted() -> None:
    rendered = as_json(report(fixture()))
    result = json.loads(rendered)
    assert result["counts"] == {"match": 0, "review": 0, "incomplete": 1}
    # All missing components remain missing, despite a known equal net field.
    assert result["verified_trade_identity"] is False
    assert result["verified_account_financial_truth"] is False
    assert result["financial_truth_status"] == "unverified"
    assert all(row["financial_truth_status"] == "unverified" for row in result["groups"])
    assert all(group["identity_status"] == "unverified" for group in result["groups"])
    assert "PRIVATE-ID" not in rendered and "SECRET-TRADE-ID" not in rendered
    assert "12.34" not in rendered
    assert "BTC/USD" not in rendered


def test_numeric_only_match_when_selection_is_explicit() -> None:
    snap = fixture()
    bot, ex, tol = parse_snapshot(snap)
    from freqtrade_truth.core.comparison import reconcile_group
    from freqtrade_truth.core.models import FinancialField
    from freqtrade_truth.core.reconciliation import group_comparable_records

    g = group_comparable_records(bot, ex, tolerance=tol)[0]
    r = reconcile_group(g, tolerance=tol, fields=(FinancialField.REPORTED_NET_PNL,))
    assert r.status.value == "match"
    # The additive report must never promote this result to verified identity.
    assert report(snap)["verified_trade_identity"] is False


def test_explicit_mismatch_not_hidden_by_missing_fields() -> None:
    r = report(fixture(exchange_amount="8.00"))
    assert r["counts"]["review"] == 1
    assert r["verified_trade_identity"] is False


def test_cross_currency_never_comparable() -> None:
    r = report(fixture(exchange_currency="USD"))
    assert r["counts"] == {"match": 0, "review": 0, "incomplete": 2}


def test_missing_and_ambiguous_candidate_rows() -> None:
    s = fixture(exchange_amount=None)
    assert report(s)["counts"]["incomplete"] == 1
    s = fixture()
    s["freqtrade"].append(dict(s["freqtrade"][0], record_id="SECOND-ID", trade_ref="S2"))
    assert report(s)["counts"]["review"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        "NaN",
        "Infinity",
        "0.01foo",
        " 2",
        "1e100000000000000",
    ],
)
def test_invalid_money_fail_closed(bad: str) -> None:
    s = fixture()
    s["freqtrade"][0]["reported_net_pnl"] = bad
    with pytest.raises((ValueError, ArithmeticError)):
        parse_snapshot(s)


def test_non_utc_rejected_and_null_never_zero() -> None:
    s = fixture()
    s["freqtrade"][0]["closed_at"] = "2026-10-22T00:00:00+02:00"
    with pytest.raises(ValueError):
        parse_snapshot(s)
    s = fixture(bot_amount="0", exchange_amount=None)
    assert report(s)["counts"]["incomplete"] == 1


def test_explicit_reveal_outputs_accounting_values_but_never_verifies_ownership() -> None:
    r = report(fixture(), values=True)
    data = as_json(r)
    assert "12.34" in data
    assert '"verified_trade_identity": false' in data
    assert "12.34" in as_csv(r)
    assert "PRIVATE-ID-bot" not in data


def test_csv_formula_injection_is_escaped() -> None:
    snap = fixture()
    for side in ("freqtrade", "exchange"):
        snap[side][0]["instrument"] = "=HYPERLINK(1,2)"
    r = report(snap, values=True)
    assert "'=HYPERLINK" in as_csv(r)


def test_valid_report_csv_and_html_redacted_and_standalone() -> None:
    r = report(fixture())
    csv_content, html_content = as_csv(r), as_html(r)
    assert "numeric_status" in csv_content and "incomplete" in csv_content
    assert "PRIVATE-ID" not in csv_content and "PRIVATE-ID" not in html_content
    assert "<script" not in html_content and "http://" not in html_content


def test_wsgi_get_only_and_not_found() -> None:
    app = make_application(report(fixture()))
    statuses: list[str] = []

    def start(status: str, headers: object) -> None:
        statuses.append(status)

    for path in ("/", "/report.json", "/report.csv", "/health"):
        body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": path}, start))
        assert body and statuses[-1] == "200 OK"
    assert b"GET only" in b"".join(app({"REQUEST_METHOD": "POST", "PATH_INFO": "/"}, start))
    assert statuses[-1].startswith("405")
    app({"REQUEST_METHOD": "GET", "PATH_INFO": "/trade/42"}, start)
    assert statuses[-1].startswith("404")


def test_cli_report_outputs_are_real(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.json"
    source.write_text(json.dumps(fixture()), encoding="utf-8")
    for fmt, expected in (
        ("json", "verified_trade_identity"),
        ("csv", "numeric_status"),
        ("html", "<!doctype html>"),
    ):
        out, err = io.StringIO(), io.StringIO()
        assert (
            main(["report", "--input", str(source), "--format", fmt], stdout=out, stderr=err) == 0
        )
        assert expected in out.getvalue()
        assert err.getvalue() == ""


def test_cli_errors_hide_file_contents(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text('{"secret":"PRIVATE-SECRET"}', encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert main(["report", "--input", str(source)], stdout=out, stderr=err) == 2
    assert "PRIVATE-SECRET" not in err.getvalue()
    assert out.getvalue() == ""
