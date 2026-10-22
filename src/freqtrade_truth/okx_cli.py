"""User-facing, account-read-only OKX source smoke check.

Explicit region and instrument mapping are required. The output is source
evidence only; it is NOT certified trade attribution or account-wide PnL.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from freqtrade_truth.adapters.okx_auth import OkxV5ReadOnlyHmacTransport
from freqtrade_truth.adapters.okx_positions_history import OkxPositionHistoryAdapter
from freqtrade_truth.core.models import ClosedTradeQuery

OFFICIAL_REGION_BASE_URLS = {
    "global": "https://www.okx.com",
    "eea": "https://eea.okx.com",
    "us": "https://us.okx.com",
}
_MAX_HOURS = 24 * 90


@dataclass(frozen=True, slots=True)
class OkxCheckConfig:
    """Qualified inputs for one bounded, source-only OKX check."""

    region: str
    inst_id: str
    inst_type: str
    settle: str
    pair: str
    hours: int
    limit: int
    json_output: bool

    def __post_init__(self) -> None:
        if self.region not in OFFICIAL_REGION_BASE_URLS:
            raise ValueError("region must be eea, global or us")
        if (
            not self.inst_id
            or self.inst_id != self.inst_id.upper()
            or any(ch.isspace() for ch in self.inst_id)
            or self.inst_type not in ("SWAP", "FUTURES")
            or not self.settle
            or self.settle != self.settle.upper()
            or any(ch.isspace() for ch in self.settle)
            or not self.pair
            or self.pair != self.pair.upper()
            or any(ch.isspace() for ch in self.pair)
            or "/" not in self.pair
            or ":" not in self.pair
        ):
            raise ValueError(
                "explicit uppercase instrument, settlement and Freqtrade pair required"
            )
        # For an expiry suffix (e.g. USD-310404), compare only the currency
        # directly before the expiry. Full qualified metadata is still a
        # prerequisite: a pair string alone cannot prove OKX settleCcy.
        pair_settle = self.pair.split(":", 1)[1].split("-", 1)[0]
        if pair_settle != self.settle:
            raise ValueError("pair and settlement currency disagree")
        if not isinstance(self.hours, int) or isinstance(self.hours, bool):
            raise ValueError("hours must be an integer")
        if self.hours < 1 or self.hours > _MAX_HOURS:
            raise ValueError(f"hours must be between 1 and {_MAX_HOURS}")
        if not isinstance(self.limit, int) or isinstance(self.limit, bool):
            raise ValueError("limit must be an integer")
        if self.limit < 1 or self.limit > 100:
            raise ValueError("limit must be between 1 and 100")


async def run_okx_check(
    config: OkxCheckConfig,
    *,
    environ: Mapping[str, str],
) -> dict[str, object]:
    """Read a qualified OKX position-history window and print only a summary."""

    key = environ.get("OKX_READ_API_KEY", "")
    secret = environ.get("OKX_READ_API_SECRET", "")
    passphrase = environ.get("OKX_READ_API_PASSPHRASE", "")
    if not key or not secret or not passphrase:
        raise ValueError(
            "OKX_READ_API_KEY, OKX_READ_API_SECRET and OKX_READ_API_PASSPHRASE "
            "must all be defined for a separately provisioned Read-only key"
        )

    transport = OkxV5ReadOnlyHmacTransport(
        OFFICIAL_REGION_BASE_URLS[config.region],
        api_key=key,
        api_secret=secret,
        api_passphrase=passphrase,
    )
    reader = OkxPositionHistoryAdapter(
        transport,
        inst_id=config.inst_id,
        inst_type=config.inst_type,
        settlement_currency=config.settle,
        normalized_instrument=config.pair,
    )

    now = datetime.now(UTC)
    rows = await reader.fetch_closed_trades(
        ClosedTradeQuery(
            closed_from=now - timedelta(hours=config.hours),
            closed_until=now,
            instrument=config.pair,
            limit=config.limit,
        )
    )
    monetary_values = tuple(r.reported_net_pnl for r in rows)
    valid_values = tuple(v for v in monetary_values if v is not None)
    # Missing values MUST NOT silently convert to a successful zero total.
    native_total: Decimal | None = (
        sum(valid_values, Decimal("0"))
        if monetary_values and len(valid_values) == len(monetary_values)
        else None
    )
    return {
        "status": "ok",
        "mode": "authenticated-read-only",
        "region": config.region,
        "inst_id": config.inst_id,
        "inst_type": config.inst_type,
        "qualified_pair_claim": config.pair,
        "settlement_currency_claim": config.settle,
        "window_hours": config.hours,
        "record_limit": config.limit,
        "closed_position_count": len(rows),
        "native_pnl_values_present": len(valid_values),
        "reported_net_native": str(native_total) if native_total is not None else None,
        "ownership_verified": False,
        "history_complete": False,
        "key_permissions_independently_verified": False,
        "scope": "OKX full-position close numeric source preview; not Freqtrade attribution",
    }
