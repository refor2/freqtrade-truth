"""Standalone bounded read-only Freqtrade smoke check, never an accounting proof."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from freqtrade_truth.adapters.freqtrade import FreqtradeReadAdapter
from freqtrade_truth.adapters.http import JsonHttpTransport, JsonObject, StdlibJsonTransport
from freqtrade_truth.core.models import ClosedTradeQuery


@dataclass(frozen=True, slots=True)
class FreqtradeCheckConfig:
    """One explicit, bounded read of the operator's own Freqtrade REST API."""

    base_url: str = "http://127.0.0.1:8080"
    hours: int = 24
    limit: int = 50
    max_pages: int = 20

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Freqtrade API URL must be an explicit http(s) origin")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("invalid Freqtrade API port") from exc
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("invalid Freqtrade API port")
        host = parsed.hostname
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if parsed.scheme == "http" and not loopback:
            raise ValueError("HTTP credentials are allowed only on literal loopback origins")
        for label, number, maximum in (
            ("hours", self.hours, 720),
            ("limit", self.limit, 100),
            ("max_pages", self.max_pages, 20),
        ):
            if (
                isinstance(number, bool)
                or not isinstance(number, int)
                or not 1 <= number <= maximum
            ):
                raise ValueError(f"{label} must be an integer in 1..{maximum}")


class _BoundedReader:
    """Prevent unlimited pagination, without claiming a partial read is complete."""

    def __init__(self, transport: JsonHttpTransport, max_pages: int) -> None:
        self.transport = transport
        self.max_pages = max_pages
        self.count = 0

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        if path != "trades":
            raise ValueError("Freqtrade smoke check accepts only the trades GET endpoint")
        if self.count >= self.max_pages:
            raise ValueError("Freqtrade page budget exceeded; data is incomplete")
        self.count += 1
        return await self.transport.get_json(path, params)


async def run_freqtrade_check(
    config: FreqtradeCheckConfig,
    *,
    environ: Mapping[str, str],
    transport: JsonHttpTransport | None = None,
) -> dict[str, object]:
    """Return redacted source capability evidence; never claim exchange reconciliation."""

    username = environ.get("FREQTRADE_READ_USERNAME", "")
    password = environ.get("FREQTRADE_READ_PASSWORD", "")
    if bool(username) != bool(password):
        raise ValueError("both FREQTRADE_READ_USERNAME and FREQTRADE_READ_PASSWORD are required")
    if transport is None:
        transport = StdlibJsonTransport(
            config.base_url,
            username=username or None,
            password=password or None,
            timeout_seconds=10.0,
        )
    reader = FreqtradeReadAdapter(_BoundedReader(transport, config.max_pages))
    now = datetime.now(UTC)
    records = await reader.fetch_closed_trades(
        ClosedTradeQuery(
            closed_from=now - timedelta(hours=config.hours),
            closed_until=now,
            limit=config.limit,
        )
    )
    return {
        "status": "ok",
        "mode": "freqtrade-read-only",
        "closed_trade_count": len(records),
        "reported_net_present": sum(r.reported_net_pnl is not None for r in records),
        "funding_present": sum(r.funding is not None for r in records),
        "record_limit": config.limit,
        "window_hours": config.hours,
        "history_complete": False,
        "exchange_ownership_verified": False,
        "account_financial_truth_verified": False,
        "values_redacted": True,
        "scope": "Freqtrade REST closed-trade source preview; not a cross-exchange audit",
    }
