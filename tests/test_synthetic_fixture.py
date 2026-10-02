import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

from freqtrade_truth.core.models import NormalizedTradeRecord, SourceKind

FIXTURE = Path(__file__).parent / "fixtures" / "normalized_trades.json"


class FixtureRecord(TypedDict):
    record_id: str
    source_kind: str
    source_name: str
    trade_ref: str
    instrument: str
    settlement_currency: str
    opened_at: str
    closed_at: str
    price_pnl: str | None
    trading_fees: str | None
    funding: str | None
    other_adjustments: str | None
    reported_net_pnl: str | None


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_optional_decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def test_synthetic_fixture_is_valid_normalized_data() -> None:
    raw_records: list[FixtureRecord] = json.loads(FIXTURE.read_text(encoding="utf-8"))

    records = [
        NormalizedTradeRecord(
            record_id=item["record_id"],
            source_kind=SourceKind(item["source_kind"]),
            source_name=item["source_name"],
            trade_ref=item["trade_ref"],
            instrument=item["instrument"],
            settlement_currency=item["settlement_currency"],
            opened_at=parse_timestamp(item["opened_at"]),
            closed_at=parse_timestamp(item["closed_at"]),
            price_pnl=parse_optional_decimal(item["price_pnl"]),
            trading_fees=parse_optional_decimal(item["trading_fees"]),
            funding=parse_optional_decimal(item["funding"]),
            other_adjustments=parse_optional_decimal(item["other_adjustments"]),
            reported_net_pnl=parse_optional_decimal(item["reported_net_pnl"]),
        )
        for item in raw_records
    ]

    assert len(records) == 2
    assert {record.source_kind for record in records} == {
        SourceKind.FREQTRADE,
        SourceKind.EXCHANGE,
    }
    assert all(record.record_id.startswith("synthetic-") for record in records)
