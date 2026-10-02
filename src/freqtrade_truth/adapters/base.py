"""Public read-only adapter protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from freqtrade_truth.core.models import (
    AdapterCapabilities,
    ClosedTradeQuery,
    NormalizedTradeRecord,
    SourceKind,
)


@runtime_checkable
class ReadOnlyTradeAdapter(Protocol):
    """Minimal contract implemented by bot-side and exchange-side readers."""

    @property
    def name(self) -> str:
        """Stable public adapter name."""

    @property
    def source_kind(self) -> SourceKind:
        """High-level origin of records emitted by this adapter."""

    def capabilities(self) -> AdapterCapabilities:
        """Declare which normalized financial fields this adapter can provide."""

    async def fetch_closed_trades(
        self,
        query: ClosedTradeQuery,
    ) -> Sequence[NormalizedTradeRecord]:
        """Fetch normalized closed-trade records without mutating account state."""
