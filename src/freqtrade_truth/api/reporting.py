"""Candidate-only, read-only JSON/CSV/HTML reports over existing comparison core."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from html import escape
from io import StringIO

from freqtrade_truth.core.comparison import reconcile_group
from freqtrade_truth.core.models import FinancialField, NormalizedTradeRecord
from freqtrade_truth.core.reconciliation import ReconciliationTolerance, group_comparable_records


def build_report(
    freqtrade: Sequence[NormalizedTradeRecord],
    exchange: Sequence[NormalizedTradeRecord],
    *,
    tolerance: ReconciliationTolerance,
    include_values: bool = False,
    fields: Sequence[FinancialField] | None = None,
) -> dict[str, object]:
    """Never promote numeric equality to verified ownership or USD parity."""
    grouped = group_comparable_records(freqtrade, exchange, tolerance=tolerance)
    rows: list[dict[str, object]] = []
    for index, group in enumerate(grouped, 1):
        result = reconcile_group(group, tolerance=tolerance, fields=fields)
        item: dict[str, object] = {
            "index": index,
            "numeric_status": result.numeric_status.value,
            "identity_status": result.identity_status,
            "financial_truth_status": result.financial_truth_status,
            "freqtrade_count": len(group.freqtrade_records),
            "exchange_count": len(group.exchange_records),
            "reason_codes": [reason.value for reason in result.reason_codes],
        }
        if include_values:
            item["instrument"] = group.instrument
            item["currency"] = group.settlement_currency
            item["comparisons"] = [
                {
                    "field": c.field.value,
                    "status": c.status.value,
                    "bot": str(c.freqtrade_value) if c.freqtrade_value is not None else None,
                    "exchange": str(c.exchange_value) if c.exchange_value is not None else None,
                    "delta": str(c.difference) if c.difference is not None else None,
                }
                for c in result.comparisons
            ]
        rows.append(item)
    return {
        "schema": "freqtrade-truth.candidate-report.v1",
        "scope": "numeric_candidate_only",
        "verified_trade_identity": False,
        "verified_account_financial_truth": False,
        "financial_truth_status": "unverified",
        "read_only": True,
        "groups": rows,
        "counts": {
            s: sum(x["numeric_status"] == s for x in rows)
            for s in ("match", "review", "incomplete")
        },
        "notice": "Numeric agreement does not prove exchange trade or account ownership.",
    }


def as_json(report: Mapping[str, object]) -> str:
    return json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def as_csv(report: Mapping[str, object]) -> str:
    out = StringIO(newline="")
    amount_fields = (
        "price_pnl",
        "trading_fees",
        "funding",
        "other_adjustments",
        "reported_net_pnl",
    )
    keys = (
        "index",
        "numeric_status",
        "identity_status",
        "financial_truth_status",
        "freqtrade_count",
        "exchange_count",
        "reason_codes",
        "instrument",
        "currency",
    ) + tuple(f"{field}_{unit}" for field in amount_fields for unit in ("bot", "exchange", "delta"))
    writer = csv.DictWriter(out, fieldnames=keys, lineterminator="\n")
    writer.writeheader()
    groups = report["groups"]
    if not isinstance(groups, list):
        raise ValueError("invalid report groups")
    for item in groups:
        if not isinstance(item, dict):
            raise ValueError("invalid report group")
        row = {key: item.get(key, "") for key in keys}
        row["reason_codes"] = ";".join(item["reason_codes"])
        for comp in item.get("comparisons", []):
            for unit in ("bot", "exchange", "delta"):
                row[f"{comp['field']}_{unit}"] = comp.get(unit)
        for key, value in row.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                row[key] = "'" + value  # Prevent spreadsheet formula execution.
        writer.writerow(row)
    return out.getvalue()


def as_html(report: Mapping[str, object]) -> str:
    """Static standalone HTML; no JS, remote assets, or executable controls."""
    rows = report["groups"]
    if not isinstance(rows, list):
        raise ValueError("invalid report groups")
    heads = ("Group", "Numeric result", "Identity", "Bot rows", "Exchange rows")
    table_head = "".join(f"<th>{escape(v)}</th>" for v in heads)
    table_body = ""
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid report group")
        fields = (
            row["index"],
            row["numeric_status"],
            row["identity_status"],
            row["freqtrade_count"],
            row["exchange_count"],
        )
        table_body += "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in fields) + "</tr>"
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<title>Freqtrade Truth Candidate Audit</title>"
        "<h1>Freqtrade Truth — candidate-only report</h1>"
        "<p><strong>UNVERIFIED</strong>: numeric equality does not verify "
        "exchange trade ownership, funding attribution or USD parity.</p>"
        f"<table border='1'><thead><tr>{table_head}</tr></thead>"
        f"<tbody>{table_body}</tbody></table></html>\n"
    )
