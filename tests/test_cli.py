import asyncio
import io
from decimal import Decimal
from collections.abc import Mapping

import pytest

import freqtrade_truth.cli as cli
from freqtrade_truth.adapters.bybit_market import BybitInverseMarketInfo


def market_info() -> BybitInverseMarketInfo:
    return BybitInverseMarketInfo(
        symbol="BTCUSD",
        base_coin="BTC",
        quote_coin="USD",
        settle_coin="BTC",
        contract_type="InversePerpetual",
        status="Trading",
        min_leverage=Decimal("1"),
        max_leverage=Decimal("100"),
        leverage_step=Decimal("0.01"),
        min_order_qty=Decimal("1"),
        qty_step=Decimal("1"),
        tick_size=Decimal("0.1"),
    )


async def fake_market_verify(
    transport: object,
    *,
    symbol: str,
    expected_base_coin: str,
    expected_quote_coin: str,
    expected_settle_coin: str,
) -> BybitInverseMarketInfo:
    del transport
    assert symbol == "BTCUSD"
    assert expected_base_coin == "BTC"
    assert expected_quote_coin == "USD"
    assert expected_settle_coin == "BTC"
    return market_info()


def config(*, authenticated: bool = False, json_output: bool = False) -> cli.BybitCheckConfig:
    return cli.BybitCheckConfig(
        symbol="BTCUSD",
        base_coin="BTC",
        quote_coin="USD",
        settle_coin="BTC",
        base_url="https://api.example.invalid",
        authenticated=authenticated,
        hours=24,
        limit=50,
        json_output=json_output,
    )


def test_public_smoke_check_returns_only_market_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "verify_bybit_inverse_perpetual", fake_market_verify)

    result = asyncio.run(cli.run_bybit_check(config(), environ={}))

    assert result["status"] == "ok"
    assert result["mode"] == "public"
    assert "authenticated" not in result
    assert result["market"] == {
        "symbol": "BTCUSD",
        "contract_type": "InversePerpetual",
        "status": "Trading",
        "base_coin": "BTC",
        "quote_coin": "USD",
        "settle_coin": "BTC",
        "min_leverage": "1",
        "max_leverage": "100",
        "leverage_step": "0.01",
        "min_order_qty": "1",
        "qty_step": "1",
        "tick_size": "0.1",
    }


def test_authenticated_mode_requires_environment_credentials_after_public_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "verify_bybit_inverse_perpetual", fake_market_verify)

    with pytest.raises(ValueError, match="BYBIT_API_KEY"):
        asyncio.run(cli.run_bybit_check(config(authenticated=True), environ={}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "btcusd"),
        ("base_coin", ""),
        ("hours", 0),
        ("hours", 169),
        ("limit", 0),
        ("limit", 101),
    ],
)
def test_config_rejects_unsafe_or_unbounded_values(field: str, value: object) -> None:
    values: dict[str, object] = {
        "symbol": "BTCUSD",
        "base_coin": "BTC",
        "quote_coin": "USD",
        "settle_coin": "BTC",
        "base_url": "https://api.example.invalid",
        "authenticated": False,
        "hours": 24,
        "limit": 50,
        "json_output": False,
    }
    values[field] = value

    with pytest.raises(ValueError):
        cli.BybitCheckConfig(**values)  # type: ignore[arg-type]


def test_cli_redacts_credentials_from_known_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = "synthetic-api-key-value"
    api_secret = "synthetic-api-secret-value"

    async def failing_check(
        check_config: cli.BybitCheckConfig,
        *,
        environ: Mapping[str, str],
    ) -> dict[str, object]:
        del check_config
        raise ValueError(
            f"synthetic failure {environ['BYBIT_API_KEY']} {environ['BYBIT_API_SECRET']}"
        )

    monkeypatch.setattr(cli, "run_bybit_check", failing_check)
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = cli.main(
        ["bybit-check", "--authenticated"],
        environ={
            "BYBIT_API_KEY": api_key,
            "BYBIT_API_SECRET": api_secret,
        },
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert api_key not in stderr.getvalue()
    assert api_secret not in stderr.getvalue()
    assert stderr.getvalue().count("[REDACTED]") == 2


def test_cli_json_output_contains_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def successful_check(
        check_config: cli.BybitCheckConfig,
        *,
        environ: Mapping[str, str],
    ) -> dict[str, object]:
        del check_config, environ
        return {
            "status": "ok",
            "mode": "public",
            "market": {
                "symbol": "BTCUSD",
                "contract_type": "InversePerpetual",
                "status": "Trading",
            },
        }

    monkeypatch.setattr(cli, "run_bybit_check", successful_check)
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = cli.main(
        ["bybit-check", "--json"],
        environ={
            "BYBIT_API_KEY": "synthetic-api-key-value",
            "BYBIT_API_SECRET": "synthetic-api-secret-value",
        },
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    assert "synthetic-api-key-value" not in stdout.getvalue()
    assert "synthetic-api-secret-value" not in stdout.getvalue()
    assert '"status": "ok"' in stdout.getvalue()
