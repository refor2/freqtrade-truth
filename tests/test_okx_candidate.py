"""Two independent GET-only sources must produce a candidate, never owner proof."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

import freqtrade_truth.okx_candidate as mod
from freqtrade_truth.core.models import NormalizedTradeRecord, SourceKind


def record(kind: SourceKind, *, ccy: str = "BTC", net: str = "2") -> NormalizedTradeRecord:
    return NormalizedTradeRecord(
        record_id=("bot1" if kind is SourceKind.FREQTRADE else "exchange1"),
        source_kind=kind,
        source_name=kind.value,
        trade_ref="opaque",
        instrument="BTC/USD:BTC",
        settlement_currency=ccy,
        opened_at=datetime(2030, 1, 1, tzinfo=UTC),
        closed_at=datetime(2030, 1, 2, tzinfo=UTC),
        price_pnl=Decimal(net) if kind is SourceKind.EXCHANGE else None,
        trading_fees=Decimal("0") if kind is SourceKind.EXCHANGE else None,
        funding=Decimal("0"),
        other_adjustments=Decimal("0") if kind is SourceKind.EXCHANGE else None,
        reported_net_pnl=Decimal(net),
    )


class FakeTransport:
    async def get_json(self, path: str, params: object = None) -> Any:
        raise AssertionError("mocked readers must not issue HTTP GETs")


class Reader:
    def __init__(self, records: list[NormalizedTradeRecord]) -> None:
        self.records = records

    async def fetch_closed_trades(self, query: object) -> list[NormalizedTradeRecord]:
        return self.records


def cfg(**changes: Any) -> mod.OkxCandidateConfig:
    params: dict[str, Any] = dict(
        freqtrade_url="http://127.0.0.1:8082",
        region="eea",
        inst_id="BTC-USD-SWAP",
        inst_type="SWAP",
        settle="BTC",
        pair="BTC/USD:BTC",
        hours=24,
        limit=50,
        max_pages=10,
        close_seconds=60,
        net_tolerance=Decimal("0"),
    )
    params.update(changes)
    return mod.OkxCandidateConfig(**params)


def test_two_source_read_only_equal_is_not_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod, "FreqtradeReadAdapter", lambda *a, **k: Reader([record(SourceKind.FREQTRADE)])
    )
    monkeypatch.setattr(
        mod, "OkxPositionHistoryAdapter", lambda *a, **k: Reader([record(SourceKind.EXCHANGE)])
    )
    result = cast(
        dict[str, Any],
        asyncio.run(
            mod.compare_okx_candidate(
                cfg(),
                environ={},
                freqtrade_transport=FakeTransport(),
                okx_transport=FakeTransport(),
            )
        ),
    )
    assert result["candidate_numeric_equal"] == 1
    assert result["candidate_numeric_different"] == 0
    assert result["verified_account_financial_truth"] is False
    assert result["history_complete"] is False
    assert result["source_currency_mismatch"] is False
    assert result["native_source_arithmetic"]["consistent"] == 1
    assert "opaque" not in str(result)
    assert "exchange1" not in str(result)


def test_known_net_difference_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod, "FreqtradeReadAdapter", lambda *a, **k: Reader([record(SourceKind.FREQTRADE, net="4")])
    )
    monkeypatch.setattr(
        mod,
        "OkxPositionHistoryAdapter",
        lambda *a, **k: Reader([record(SourceKind.EXCHANGE, net="2")]),
    )
    result = cast(
        dict[str, Any],
        asyncio.run(
            mod.compare_okx_candidate(
                cfg(),
                environ={},
                freqtrade_transport=FakeTransport(),
                okx_transport=FakeTransport(),
            )
        ),
    )
    assert result["candidate_numeric_different"] == 1
    assert result["verified_account_financial_truth"] is False


def test_currency_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod, "FreqtradeReadAdapter", lambda *a, **k: Reader([record(SourceKind.FREQTRADE)])
    )
    monkeypatch.setattr(
        mod,
        "OkxPositionHistoryAdapter",
        lambda *a, **k: Reader([record(SourceKind.EXCHANGE, ccy="USDC")]),
    )
    result = cast(
        dict[str, Any],
        asyncio.run(
            mod.compare_okx_candidate(
                cfg(),
                environ={},
                freqtrade_transport=FakeTransport(),
                okx_transport=FakeTransport(),
            )
        ),
    )
    assert result["source_currency_mismatch"] is True
    assert result["candidate_numeric_equal"] == 0
    assert result["unverified_or_ambiguous_groups"] >= 1


def test_missing_read_only_credentials_fail_before_network() -> None:
    with pytest.raises(ValueError, match="Read-only"):
        asyncio.run(mod.compare_okx_candidate(cfg(), environ={}))


@pytest.mark.parametrize(
    "bad",
    [
        {"hours": 1000},
        {"limit": 0},
        {"max_pages": 0},
        {"net_tolerance": Decimal("NaN")},
        {"close_seconds": -1},
        {"source_amount_ccy": "USDC"},
        {"freqtrade_url": "http://example.com"},
    ],
)
def test_bad_config_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        cfg(**bad)


def test_any_foreign_currency_blocks_candidate_equality(monkeypatch: pytest.MonkeyPatch) -> None:
    bot_rows = [record(SourceKind.FREQTRADE)]
    exchange_rows = [record(SourceKind.EXCHANGE)]
    exchange_rows.append(record(SourceKind.EXCHANGE, ccy="USDC"))
    # A matching record must not conceal an extra contradictory exchange unit.
    from dataclasses import replace

    exchange_rows[-1] = replace(exchange_rows[-1], record_id="exchange-other-currency")
    monkeypatch.setattr(mod, "FreqtradeReadAdapter", lambda *a, **k: Reader(bot_rows))
    monkeypatch.setattr(mod, "OkxPositionHistoryAdapter", lambda *a, **k: Reader(exchange_rows))
    result = cast(
        dict[str, Any],
        asyncio.run(
            mod.compare_okx_candidate(
                cfg(),
                environ={},
                freqtrade_transport=FakeTransport(),
                okx_transport=FakeTransport(),
            )
        ),
    )
    assert result["source_currency_mismatch"] is True
    assert result["candidate_numeric_equal"] == 0
    assert result["unverified_or_ambiguous_groups"] >= 1


def test_ambiguous_multiple_bot_candidates_are_not_numeric_differences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    bot_rows = [record(SourceKind.FREQTRADE)]
    bot_rows.append(replace(bot_rows[0], record_id="bot-other"))
    monkeypatch.setattr(mod, "FreqtradeReadAdapter", lambda *a, **k: Reader(bot_rows))
    monkeypatch.setattr(
        mod, "OkxPositionHistoryAdapter", lambda *a, **k: Reader([record(SourceKind.EXCHANGE)])
    )
    result = cast(
        dict[str, Any],
        asyncio.run(
            mod.compare_okx_candidate(
                cfg(),
                environ={},
                freqtrade_transport=FakeTransport(),
                okx_transport=FakeTransport(),
            )
        ),
    )
    assert result["candidate_numeric_different"] == 0
    assert result["unverified_or_ambiguous_groups"] >= 1

@pytest.mark.parametrize("limited_source", ["freqtrade", "okx"])
def test_candidate_must_fail_closed_when_source_result_reaches_limit(
    monkeypatch: pytest.MonkeyPatch, limited_source: str,
) -> None:
    from dataclasses import replace

    bot = [record(SourceKind.FREQTRADE)]
    exchange = [record(SourceKind.EXCHANGE)]
    if limited_source == "freqtrade":
        bot.append(replace(bot[0], record_id="additional-bot-trade"))
    else:
        exchange.append(replace(exchange[0], record_id="additional-exchange-position"))
    monkeypatch.setattr(mod, "FreqtradeReadAdapter", lambda *a, **k: Reader(bot))
    monkeypatch.setattr(mod, "OkxPositionHistoryAdapter", lambda *a, **k: Reader(exchange))
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(
            mod.compare_okx_candidate(
                cfg(limit=2),
                environ={},
                freqtrade_transport=FakeTransport(),
                okx_transport=FakeTransport(),
            )
        )
