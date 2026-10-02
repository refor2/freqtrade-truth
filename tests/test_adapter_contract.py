import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from freqtrade_truth.adapters.base import ReadOnlyTradeAdapter
from freqtrade_truth.adapters.bybit import BybitInverseClosedPnlAdapter
from freqtrade_truth.adapters.freqtrade import FreqtradeReadAdapter
from freqtrade_truth.adapters.http import JsonObject
from freqtrade_truth.core.models import (
    AdapterCapabilities,
    ClosedTradeQuery,
    FinancialField,
    NormalizedTradeRecord,
    SourceKind,
)

FINANCIAL_FIELD_ATTRS = {
    FinancialField.PRICE_PNL: "price_pnl",
    FinancialField.TRADING_FEES: "trading_fees",
    FinancialField.FUNDING: "funding",
    FinancialField.OTHER_ADJUSTMENTS: "other_adjustments",
    FinancialField.REPORTED_NET_PNL: "reported_net_pnl",
}


class SyntheticReadAdapter:
    @property
    def name(self) -> str:
        return "synthetic"

    @property
    def source_kind(self) -> SourceKind:
        return SourceKind.EXCHANGE

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            fields=frozenset(
                {
                    FinancialField.PRICE_PNL,
                    FinancialField.TRADING_FEES,
                    FinancialField.REPORTED_NET_PNL,
                }
            )
        )

    async def fetch_closed_trades(
        self,
        query: ClosedTradeQuery,
    ) -> Sequence[NormalizedTradeRecord]:
        del query
        return (
            NormalizedTradeRecord(
                record_id="synthetic-record",
                source_kind=SourceKind.EXCHANGE,
                source_name=self.name,
                trade_ref="synthetic-trade",
                instrument="BTC/USDT:USDT",
                settlement_currency="USDT",
                opened_at=datetime(2030, 1, 1, 8, tzinfo=UTC),
                closed_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
                price_pnl=Decimal("5.00"),
                trading_fees=Decimal("-0.10"),
                reported_net_pnl=Decimal("4.90"),
            ),
        )


class StaticFreqtradeTransport:
    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        del path, params
        return {
            "trades": [
                {
                    "trade_id": 1001,
                    "pair": "BTC/USDT:USDT",
                    "quote_currency": "USDT",
                    "is_open": False,
                    "open_timestamp": 1893484800000,
                    "close_timestamp": 1893499200000,
                    "profit_abs": Decimal("4.90"),
                    "funding_fees": Decimal("-0.10"),
                }
            ],
            "trades_count": 1,
            "offset": 0,
            "total_trades": 1,
        }


class StaticBybitTransport:
    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        del path, params
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "category": "inverse",
                "list": [
                    {
                        "symbol": "BTCUSD",
                        "orderId": "synthetic-order",
                        "updatedTime": "1893499200000",
                        "closedPnl": "0.00120",
                        "openFee": "0.00001",
                        "closeFee": "0.00002",
                    }
                ],
                "nextPageCursor": "",
            },
            "time": 0,
        }


@dataclass(frozen=True, slots=True)
class AdapterContractCase:
    name: str
    adapter: ReadOnlyTradeAdapter
    query: ClosedTradeQuery


def contract_query() -> ClosedTradeQuery:
    return ClosedTradeQuery(
        closed_from=datetime(2030, 1, 1, tzinfo=UTC),
        closed_until=datetime(2030, 1, 2, tzinfo=UTC),
        instrument="BTC/USDT:USDT",
        limit=10,
    )


CASES = (
    AdapterContractCase(
        name="synthetic",
        adapter=SyntheticReadAdapter(),
        query=contract_query(),
    ),
    AdapterContractCase(
        name="freqtrade",
        adapter=FreqtradeReadAdapter(StaticFreqtradeTransport()),
        query=contract_query(),
    ),
    AdapterContractCase(
        name="bybit-inverse",
        adapter=BybitInverseClosedPnlAdapter(
            StaticBybitTransport(),
            symbol="BTCUSD",
            settlement_currency="BTC",
        ),
        query=contract_query(),
    ),
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_read_only_adapter_contract(case: AdapterContractCase) -> None:
    adapter = case.adapter

    assert isinstance(adapter, ReadOnlyTradeAdapter)
    assert adapter.name.strip()
    assert isinstance(adapter.source_kind, SourceKind)

    capabilities = adapter.capabilities()
    assert isinstance(capabilities, AdapterCapabilities)
    assert capabilities.fields <= frozenset(FinancialField)

    records = asyncio.run(adapter.fetch_closed_trades(case.query))

    assert isinstance(records, Sequence)
    assert len(records) <= (case.query.limit or len(records))

    for record in records:
        assert isinstance(record, NormalizedTradeRecord)
        assert record.source_name == adapter.name
        assert record.source_kind is adapter.source_kind
        assert case.query.closed_from <= record.closed_at <= case.query.closed_until

        if case.query.instrument is not None:
            assert record.instrument == case.query.instrument

        for financial_field, attribute in FINANCIAL_FIELD_ATTRS.items():
            value = getattr(record, attribute)
            if value is not None:
                assert capabilities.supports(financial_field)


def test_contract_cases_return_records() -> None:
    for case in CASES:
        records = asyncio.run(case.adapter.fetch_closed_trades(case.query))
        assert records
