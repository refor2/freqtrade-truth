import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from freqtrade_truth.adapters.base import ReadOnlyTradeAdapter
from freqtrade_truth.core.models import (
    AdapterCapabilities,
    ClosedTradeQuery,
    FinancialField,
    NormalizedTradeRecord,
    SourceKind,
)


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


def test_structural_adapter_protocol() -> None:
    adapter = SyntheticReadAdapter()

    assert isinstance(adapter, ReadOnlyTradeAdapter)
    assert adapter.source_kind is SourceKind.EXCHANGE


def test_read_adapter_returns_normalized_records() -> None:
    adapter = SyntheticReadAdapter()
    query = ClosedTradeQuery(
        closed_from=datetime(2030, 1, 1, tzinfo=UTC),
        closed_until=datetime(2030, 1, 2, tzinfo=UTC),
    )

    records = asyncio.run(adapter.fetch_closed_trades(query))

    assert len(records) == 1
    assert records[0].reported_net_pnl == Decimal("4.90")
