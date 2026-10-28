"""Prevent unbounded or widened private API reads before any transport call."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from freqtrade_truth.adapters.http import JsonObject
from freqtrade_truth.bybit_candidate import _BoundedBybit

START = 1_800_000_000_000
END = START + 60_000


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str | int | bool] | None]] = []

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        self.calls.append((path, params))
        return {"retCode": 0, "result": {}}


VALID: dict[str, dict[str, str | int | bool]] = {
    "v5/position/closed-pnl": {
        "category": "inverse",
        "symbol": "BTCUSD",
        "startTime": START,
        "endTime": END,
        "limit": 50,
    },
    "v5/execution/list": {
        "category": "inverse",
        "orderId": "id-1",
        "execType": "Trade",
        "startTime": START,
        "endTime": END,
        "limit": 50,
    },
    "v5/account/transaction-log": {
        "accountType": "UNIFIED",
        "category": "inverse",
        "currency": "BTC",
        "startTime": START,
        "endTime": END,
        "limit": 50,
    },
}


@pytest.mark.parametrize("path", tuple(VALID))
def test_bounded_private_get_accepts_only_explicitly_scoped_windows(path: str) -> None:
    transport = RecordingTransport()
    params = VALID[path]
    asyncio.run(_BoundedBybit(transport, max_pages=3).get_json(path, params))
    assert transport.calls == [(path, params)]


@pytest.mark.parametrize("path", tuple(VALID))
@pytest.mark.parametrize(
    "change",
    [
        {"startTime": None},
        {"endTime": None},
        {"startTime": END + 1},
        {"endTime": START + 7 * 24 * 60 * 60 * 1000},
        {"limit": 0},
        {"limit": True},
        {"extraPrivateField": "not-allowed"},
    ],
)
def test_bounded_private_get_rejects_widened_or_malformed_queries_before_io(
    path: str, change: dict[str, object]
) -> None:
    transport = RecordingTransport()
    params: dict[str, object] = dict(VALID[path])
    for key, value in change.items():
        if value is None:
            del params[key]
        else:
            params[key] = value
    with pytest.raises(ValueError):
        asyncio.run(_BoundedBybit(transport, max_pages=3).get_json(path, params))  # type: ignore[arg-type]
    assert transport.calls == []


@pytest.mark.parametrize(
    "path,wrong",
    [
        ("v5/position/closed-pnl", {"symbol": ""}),
        ("v5/execution/list", {"orderId": ""}),
        ("v5/execution/list", {"execType": "Funding"}),
        ("v5/account/transaction-log", {"accountType": "CONTRACT"}),
        ("v5/account/transaction-log", {"currency": ""}),
    ],
)
def test_bounded_private_get_rejects_missing_identity_scope(
    path: str, wrong: dict[str, str]
) -> None:
    transport = RecordingTransport()
    params = dict(VALID[path])
    params.update(wrong)
    with pytest.raises(ValueError):
        asyncio.run(_BoundedBybit(transport, max_pages=3).get_json(path, params))
    assert not transport.calls
