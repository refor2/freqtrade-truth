"""The first runnable OKX end-user path: no orders, no fabricated ownership."""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import freqtrade_truth.cli as cli
import freqtrade_truth.okx_cli as okx_cli
from freqtrade_truth.adapters.okx_auth import OkxV5ReadOnlyHmacTransport
from freqtrade_truth.core.models import NormalizedTradeRecord, SourceKind


def args(*extras: str) -> list[str]:
    return [
        "okx-check",
        "--region",
        "eea",
        "--inst-id",
        "BTC-USD-SWAP",
        "--inst-type",
        "SWAP",
        "--settle",
        "BTC",
        "--pair",
        "BTC/USD:BTC",
        "--hours",
        "24",
        "--limit",
        "25",
        "--json",
        *extras,
    ]


def env() -> dict[str, str]:
    return {
        "OKX_READ_API_KEY": "dummy-example-key",
        "OKX_READ_API_SECRET": "dummy-example-secret",
        "OKX_READ_API_PASSPHRASE": "dummy-example-passphrase",
    }


def row(*, reported: Decimal | None = Decimal("0.012")) -> NormalizedTradeRecord:
    return NormalizedTradeRecord(
        record_id="okx:position:synthetic-777",
        trade_ref="synthetic-777",
        source_kind=SourceKind.EXCHANGE,
        source_name="okx",
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        opened_at=datetime(2030, 1, 1, tzinfo=UTC),
        closed_at=datetime(2030, 1, 2, tzinfo=UTC),
        reported_net_pnl=reported,
    )


def test_eea_and_global_transport_regions_allow_only_official_sites() -> None:
    for url in ("https://eea.okx.com", "https://www.okx.com", "https://us.okx.com"):
        instance = OkxV5ReadOnlyHmacTransport(
            url, api_key="dummy", api_secret="dummy", api_passphrase="dummy"
        )
        assert instance._hostname == url.removeprefix("https://")  # no real request


@pytest.mark.parametrize(
    "url",
    (
        "https://eea.okx.com.evil.example",
        "http://eea.okx.com",
        "https://bad.example",
        "https://foo@eea.okx.com",
        "https://eea.okx.com/private",
    ),
)
def test_region_transport_fails_closed_for_untrusted_sites(url: str) -> None:
    with pytest.raises(ValueError):
        OkxV5ReadOnlyHmacTransport(url, api_key="dummy", api_secret="dummy", api_passphrase="dummy")


def test_okx_first_run_uses_injected_transport_without_trading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, object]] = []

    class FakeReader:
        def __init__(self, transport: object, **kw: object) -> None:
            observed.append({"transport": transport, **kw})

        async def fetch_closed_trades(self, query: object) -> tuple[NormalizedTradeRecord, ...]:
            observed.append({"query": query})
            return (row(),)

    monkeypatch.setattr(okx_cli, "OkxPositionHistoryAdapter", FakeReader)
    result = asyncio.run(
        okx_cli.run_okx_check(
            okx_cli.OkxCheckConfig(
                region="eea",
                inst_id="BTC-USD-SWAP",
                inst_type="SWAP",
                settle="BTC",
                pair="BTC/USD:BTC",
                hours=24,
                limit=25,
                json_output=True,
            ),
            environ=env(),
        )
    )
    assert result["status"] == "ok"
    assert result["region"] == "eea"
    assert result["mode"] == "authenticated-read-only"
    assert result["closed_position_count"] == 1
    assert result["reported_net_native"] == "0.012"
    assert result["ownership_verified"] is False
    assert result["history_complete"] is False
    assert observed[0]["inst_type"] == "SWAP"
    assert observed[0]["settlement_currency"] == "BTC"
    assert observed[0]["normalized_instrument"] == "BTC/USD:BTC"
    transport = observed[0]["transport"]
    assert isinstance(transport, OkxV5ReadOnlyHmacTransport)
    assert transport._hostname == "eea.okx.com"


def test_no_credentials_means_no_network_or_account_access() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(args(), environ={}, stdout=out, stderr=err)
    assert code == 2
    assert out.getvalue() == ""
    assert "OKX_READ" in err.getvalue()


def test_cli_user_summary_contains_no_raw_position_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(cfg: object, *, environ: Mapping[str, str]) -> dict[str, object]:
        assert environ == env()
        return {
            "status": "ok",
            "region": "eea",
            "mode": "authenticated-read-only",
            "closed_position_count": 1,
            "reported_net_native": "0.012",
            "ownership_verified": False,
            "history_complete": False,
        }

    monkeypatch.setattr(cli, "run_okx_check", fake)
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(args(), environ=env(), stdout=out, stderr=err)
    assert code == 0
    assert err.getvalue() == ""
    result = json.loads(out.getvalue())
    assert result["ownership_verified"] is False
    assert "dummy-example" not in out.getvalue()
    assert "synthetic-777" not in out.getvalue()


def test_known_error_never_echoes_environment_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(cfg: object, *, environ: Mapping[str, str]) -> dict[str, object]:
        del cfg
        raise ValueError("bad credential " + environ["OKX_READ_API_SECRET"])

    monkeypatch.setattr(cli, "run_okx_check", failing)
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(args(), environ=env(), stdout=out, stderr=err)
    assert code == 2
    assert out.getvalue() == ""
    assert "dummy-example-secret" not in err.getvalue()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "24.1"])
def test_invalid_window_rejected(value: str) -> None:
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(args("--hours", value), environ=env(), stdout=out, stderr=err) != 0
    assert out.getvalue() == ""


def test_unsupported_region_and_pair_fail_before_network() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(args("--region", "untrusted"), environ=env(), stdout=out, stderr=err)
    assert code != 0
    assert out.getvalue() == ""


def test_null_native_net_is_not_summed_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reader:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def fetch_closed_trades(self, q: object) -> tuple[NormalizedTradeRecord, ...]:
            return (row(reported=None),)

    monkeypatch.setattr(okx_cli, "OkxPositionHistoryAdapter", Reader)
    res = asyncio.run(
        okx_cli.run_okx_check(
            okx_cli.OkxCheckConfig(
                region="eea",
                inst_id="BTC-USD-SWAP",
                inst_type="SWAP",
                settle="BTC",
                pair="BTC/USD:BTC",
                hours=24,
                limit=25,
                json_output=True,
            ),
            environ=env(),
        )
    )
    assert res["reported_net_native"] is None
    assert res["native_pnl_values_present"] == 0


def test_full_signed_get_reader_chain_synthetic_eea(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real signed transport + actual OKX decoder; only HTTP I/O is simulated."""
    from datetime import timedelta

    import freqtrade_truth.adapters.okx_auth as auth

    now = datetime.now(UTC)
    row_data = {
        "posId": "synthetic:100",
        "uTime": str(int(now.timestamp() * 1000)),
        "cTime": str(int((now - timedelta(hours=2)).timestamp() * 1000)),
        "instId": "BTC-USD-SWAP",
        "instType": "SWAP",
        "ccy": "BTC",
        "type": "2",
        "pnl": "0.2",
        "fee": "-0.01",
        "fundingFee": "-0.02",
        "liqPenalty": "0",
        "settledPnl": "0",
        "realizedPnl": "0.17",
    }
    request_log: list[tuple[str, str, str]] = []

    class Response:
        status = 200

        def read(self, size: int) -> bytes:
            assert size > 0
            return json.dumps({"code": "0", "msg": "", "data": [row_data]}).encode()

        def close(self) -> None:
            pass

    class Connection:
        def __init__(self, host: str, port: int | None, timeout: float) -> None:
            assert port is None
            assert timeout > 0
            self.host = host

        def request(self, method: str, target: str, headers: dict[str, str]) -> None:
            assert headers["OK-ACCESS-SIGN"]
            request_log.append((self.host, method, target))

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(auth, "HTTPSConnection", Connection)
    config = okx_cli.OkxCheckConfig(
        region="eea",
        inst_id="BTC-USD-SWAP",
        inst_type="SWAP",
        settle="BTC",
        pair="BTC/USD:BTC",
        hours=24,
        limit=50,
        json_output=True,
    )
    result = asyncio.run(okx_cli.run_okx_check(config, environ=env()))
    assert result["reported_net_native"] == "0.17"
    assert result["closed_position_count"] == 1
    assert request_log == [
        (
            "eea.okx.com",
            "GET",
            "/api/v5/account/positions-history?instType=SWAP&instId=BTC-USD-SWAP&limit=100",
        )
    ]
    assert "synthetic:100" not in json.dumps(result)


def test_okx_report_counts_native_arithmetic_but_never_verified_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    class FullReader:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def fetch_closed_trades(self, query: object) -> tuple[NormalizedTradeRecord, ...]:
            first = replace(
                row(),
                price_pnl=Decimal("0.02"),
                trading_fees=Decimal("-0.005"),
                funding=Decimal("-0.003"),
                other_adjustments=Decimal("0"),
                reported_net_pnl=Decimal("0.012"),
            )
            second = replace(row(), record_id="second", trade_ref="second", reported_net_pnl=None)
            return first, second

    monkeypatch.setattr(okx_cli, "OkxPositionHistoryAdapter", FullReader)
    res = asyncio.run(
        okx_cli.run_okx_check(
            okx_cli.OkxCheckConfig(
                region="eea",
                inst_id="BTC-USD-SWAP",
                inst_type="SWAP",
                settle="BTC",
                pair="BTC/USD:BTC",
                hours=24,
                limit=25,
                json_output=True,
            ),
            environ=env(),
        )
    )
    assert res["native_accounting_counts"] == {"consistent": 1, "different": 0, "incomplete": 1}
    assert res["reported_net_native"] is None
    assert res["ownership_verified"] is False


def test_qualified_isolated_xperp_native_usdc_cli() -> None:
    config = okx_cli.OkxCheckConfig(
        region="eea",
        inst_id="BTC-USD-310404",
        inst_type="FUTURES",
        settle="USD",
        pair="BTC/USD:USD-310404",
        hours=24,
        limit=25,
        json_output=True,
        source_amount_ccy="USDC",
        isolated_xperp=True,
    )
    assert config.source_amount_ccy == "USDC"
    assert config.settle == "USD"


@pytest.mark.parametrize(
    ("native", "xperp", "kind", "inst_id"),
    [
        ("USDC", False, "FUTURES", "BTC-USD-310404"),
        ("USDT", True, "FUTURES", "BTC-USD-310404"),
        ("USDC", True, "SWAP", "BTC-USD-SWAP"),
        ("USDC", True, "FUTURES", "BTC-USD-SWAP"),
    ],
)
def test_unqualified_xperp_currency_override_rejected(
    native: str,
    xperp: bool,
    kind: str,
    inst_id: str,
) -> None:
    with pytest.raises(ValueError, match="xperp|native"):
        okx_cli.OkxCheckConfig(
            region="eea",
            inst_id=inst_id,
            inst_type=kind,
            settle="USD",
            pair="BTC/USD:USD-310404",
            hours=24,
            limit=25,
            json_output=True,
            source_amount_ccy=native,
            isolated_xperp=xperp,
        )


def test_xperp_cli_passes_qualified_usdc_to_actual_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class Reader:
        def __init__(self, transport: object, **options: object) -> None:
            seen.update(options)

        async def fetch_closed_trades(self, query: object) -> tuple[()]:
            return ()

    monkeypatch.setattr(okx_cli, "OkxPositionHistoryAdapter", Reader)

    async def approved_preflight(**kwargs: object) -> None:
        assert kwargs["inst_type"] == "FUTURES"
        assert kwargs["settle"] == "USD"

    monkeypatch.setattr(okx_cli, "require_qualified_isolated_xperp", approved_preflight)
    cfg = okx_cli.OkxCheckConfig(
        region="eea",
        inst_id="BTC-USD-310404",
        inst_type="FUTURES",
        settle="USD",
        pair="BTC/USD:USD-310404",
        hours=24,
        limit=25,
        json_output=True,
        source_amount_ccy="USDC",
        isolated_xperp=True,
    )
    result = asyncio.run(okx_cli.run_okx_check(cfg, environ=env()))
    assert seen["settlement_currency"] == "USDC"
    assert seen["isolated_xperp"] is True
    assert result["settlement_currency_claim"] == "USD"
    assert result["source_amount_currency_claim"] == "USDC"
    assert result["ownership_verified"] is False
    assert result["reported_net_native"] is None
