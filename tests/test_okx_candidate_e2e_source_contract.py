"""Synthetic end-to-end adapter composition; no external account or network access."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from freqtrade_truth.adapters.http import JsonObject
from freqtrade_truth.okx_candidate import OkxCandidateConfig, compare_okx_candidate


class Source:
    def __init__(self, payload: JsonObject, path: str) -> None:
        self.payload = payload
        self.path = path
        self.requests: list[str] = []

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        assert path == self.path
        assert isinstance(params, Mapping)
        self.requests.append(path)
        return self.payload


def sources(*, funding: str | None = "-0.25", net: str = "2.25") -> tuple[Source, Source]:
    now = datetime.now(UTC)
    closed = str(int((now - timedelta(hours=1)).timestamp() * 1000))
    opened = str(int((now - timedelta(hours=2)).timestamp() * 1000))
    ft = Source(
        cast(
            JsonObject,
            {
                "offset": 0,
                "total_trades": 1,
                "trades": [
                    {
                        "trade_id": 100,
                        "is_open": False,
                        "pair": "BTC/USD:BTC",
                        "quote_currency": "USD",
                        "close_timestamp": int(closed),
                        "open_timestamp": int(opened),
                        "profit_abs": net,
                        "funding_fees": funding,
                    }
                ],
            },
        ),
        "trades",
    )
    okx = Source(
        cast(
            JsonObject,
            {
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "instId": "BTC-USD-SWAP",
                        "instType": "SWAP",
                        "ccy": "BTC",
                        "posId": "pos-synthetic",
                        "type": "2",
                        "uTime": closed,
                        "cTime": opened,
                        "pnl": "3.00",
                        "fee": "-0.50",
                        "fundingFee": funding if funding is not None else "",
                        "liqPenalty": "0",
                        "settledPnl": "0",
                        "realizedPnl": net,
                    }
                ],
            },
        ),
        "api/v5/account/positions-history",
    )
    return ft, okx


def cfg() -> OkxCandidateConfig:
    return OkxCandidateConfig(
        freqtrade_url="http://127.0.0.1:8082",
        region="eea",
        inst_id="BTC-USD-SWAP",
        inst_type="SWAP",
        settle="BTC",
        pair="BTC/USD:BTC",
        hours=24,
        limit=10,
        max_pages=2,
        close_seconds=5,
    )


def test_e2e_two_real_parsers_agree_but_never_claim_owner() -> None:
    ft, okx = sources()
    out = asyncio.run(
        compare_okx_candidate(cfg(), environ={}, freqtrade_transport=ft, okx_transport=okx)
    )
    assert out["freqtrade_record_count"] == 1
    assert out["okx_record_count"] == 1
    assert out["candidate_numeric_equal"] == 1
    assert out["candidate_numeric_different"] == 0
    assert cast(dict[str, int], out["native_source_arithmetic"])["consistent"] == 1
    assert out["verified_account_financial_truth"] is False
    assert out["funding_ownership_verified"] is False
    assert out["usd_valuation_verified"] is False
    assert ft.requests == ["trades"]
    assert okx.requests == ["api/v5/account/positions-history"]
    assert "pos-synthetic" not in str(out)


def test_missing_native_funding_never_qualifies_numeric_match() -> None:
    ft, okx = sources(funding=None)
    out = asyncio.run(
        compare_okx_candidate(cfg(), environ={}, freqtrade_transport=ft, okx_transport=okx)
    )
    assert cast(dict[str, int], out["native_source_arithmetic"])["incomplete"] == 1
    assert out["candidate_numeric_equal"] == 0
    assert cast(int, out["unverified_or_ambiguous_groups"]) >= 1


def test_contradictory_source_equation_raises_without_false_match() -> None:
    ft, okx = sources(net="1.25")
    with pytest.raises(Exception, match="equation"):
        asyncio.run(
            compare_okx_candidate(cfg(), environ={}, freqtrade_transport=ft, okx_transport=okx)
        )
