"""Synthetic GET-only OKX metadata qualification before private XPERP read."""

import asyncio
import json

import pytest

import freqtrade_truth.okx_instruments as mod


def instrument(**changes: str) -> dict[str, str]:
    raw = {
        "instType": "FUTURES",
        "instId": "BTC-USD-310404",
        "ruleType": "xperp",
        "ctType": "linear",
        "settleCcy": "USD",
        "state": "live",
    }
    raw.update(changes)
    return raw


class Response:
    def __init__(self, *, status: int = 200, payload: dict[str, object] | None = None):
        self.status = status
        self.payload = (
            payload
            if payload is not None
            else {
                "code": "0",
                "data": [instrument()],
                "msg": "",
            }
        )
        self.closed = False

    def read(self, n: int) -> bytes:
        return json.dumps(self.payload).encode("utf-8")[:n]

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    response = Response()
    calls: list[tuple[str, str, str]] = []
    headers_seen: list[dict[str, str]] = []

    def __init__(self, host: str, port: int | None, timeout: float):
        assert port is None and timeout > 0
        self.host = host

    def request(self, method: str, target: str, headers: dict[str, str]) -> None:
        assert "OK-ACCESS-KEY" not in headers
        self.__class__.calls.append((self.host, method, target))
        self.__class__.headers_seen.append(dict(headers))

    def getresponse(self) -> Response:
        return self.__class__.response

    def close(self) -> None:
        pass


def check(monkeypatch: pytest.MonkeyPatch, response: Response | None = None) -> None:
    FakeConnection.calls.clear()
    FakeConnection.headers_seen.clear()
    FakeConnection.response = response or Response()
    monkeypatch.setattr(mod, "HTTPSConnection", FakeConnection)
    asyncio.run(
        mod.require_qualified_isolated_xperp(
            region="eea",
            inst_id="BTC-USD-310404",
            inst_type="FUTURES",
            settle="USD",
        )
    )


def test_official_eea_public_instrument_preflight_exact_get_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    res = Response()
    check(monkeypatch, res)
    assert FakeConnection.calls == [
        (
            "eea.okx.com",
            "GET",
            "/api/v5/public/instruments?instType=FUTURES&instId=BTC-USD-310404",
        )
    ]
    assert FakeConnection.headers_seen[0]["User-Agent"] == "freqtrade-truth/0.1.0"
    assert res.closed


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"instType": "SWAP"}, "instType"),
        ({"instId": "ETH-USD-310404"}, "instId"),
        ({"ruleType": "pre_market"}, "ruleType"),
        ({"ruleType": "normal"}, "ruleType"),
        ({"ctType": "inverse"}, "ctType"),
        ({"settleCcy": "USDC"}, "settleCcy"),
        ({"state": "suspend"}, "state"),
    ],
)
def test_metadata_source_contradiction_blocks_account_query(
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, str],
    expected: str,
) -> None:
    with pytest.raises(mod.OkxInstrumentPreflightError, match=expected):
        check(monkeypatch, Response(payload={"code": "0", "data": [instrument(**change)]}))


@pytest.mark.parametrize(
    "response",
    [
        Response(payload={"code": "0", "data": []}),
        Response(payload={"code": "0", "data": [instrument(), instrument()]}),
        Response(payload={"code": "50011", "data": [instrument()]}),
        Response(payload={"code": "0", "data": "wrong"}),
        Response(status=302),
        Response(status=403),
    ],
)
def test_missing_ambiguous_or_error_metadata_blocks_account_query(
    monkeypatch: pytest.MonkeyPatch,
    response: Response,
) -> None:
    with pytest.raises(mod.OkxInstrumentPreflightError):
        check(monkeypatch, response)


def test_only_known_regions_accepted_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeConnection.calls.clear()
    monkeypatch.setattr(mod, "HTTPSConnection", FakeConnection)
    with pytest.raises(mod.OkxInstrumentPreflightError):
        asyncio.run(
            mod.require_qualified_isolated_xperp(
                region="untrusted",
                inst_id="BTC-USD-310404",
                inst_type="FUTURES",
                settle="USD",
            )
        )
    assert FakeConnection.calls == []


def test_no_public_preflight_for_plain_okx_swaps(monkeypatch: pytest.MonkeyPatch) -> None:
    import freqtrade_truth.okx_cli as cli
    from freqtrade_truth.core.models import NormalizedTradeRecord

    class EmptyReader:
        def __init__(self, *_: object, **__: object):
            pass

        async def fetch_closed_trades(self, *_: object) -> tuple[NormalizedTradeRecord, ...]:
            return ()

    async def preflight(*_: object, **__: object) -> None:
        raise AssertionError("plain SWAP must not call special XPERP preflight")

    monkeypatch.setattr(cli, "require_qualified_isolated_xperp", preflight)
    monkeypatch.setattr(cli, "OkxPositionHistoryAdapter", EmptyReader)
    cfg = cli.OkxCheckConfig(
        region="eea",
        inst_id="BTC-USD-SWAP",
        inst_type="SWAP",
        settle="BTC",
        pair="BTC/USD:BTC",
        hours=24,
        limit=25,
        json_output=True,
    )
    env = {
        "OKX_READ_API_KEY": "dummy-key",
        "OKX_READ_API_SECRET": "dummy-secret",
        "OKX_READ_API_PASSPHRASE": "dummy-passphrase",
    }
    out = asyncio.run(cli.run_okx_check(cfg, environ=env))
    assert out["closed_position_count"] == 0


def test_xperp_preflight_happens_before_private_transport_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import freqtrade_truth.okx_cli as cli

    markers: list[str] = []

    async def fail_preflight(*_: object, **__: object) -> None:
        markers.append("preflight")
        raise mod.OkxInstrumentPreflightError("metadata failure")

    class ForbiddenTransport:
        def __init__(self, *_: object, **__: object):
            raise AssertionError("no signed account call before metadata verification")

    monkeypatch.setattr(cli, "require_qualified_isolated_xperp", fail_preflight)
    monkeypatch.setattr(cli, "OkxV5ReadOnlyHmacTransport", ForbiddenTransport)
    cfg = cli.OkxCheckConfig(
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
    env = {
        "OKX_READ_API_KEY": "dummy-key",
        "OKX_READ_API_SECRET": "dummy-secret",
        "OKX_READ_API_PASSPHRASE": "dummy-passphrase",
    }
    with pytest.raises(mod.OkxInstrumentPreflightError):
        asyncio.run(cli.run_okx_check(cfg, environ=env))
    assert markers == ["preflight"]


def test_real_okx_eea_xperp_identifier_with_underscores_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_public_shape = "BTC-USD_UM_XPERP-310404"
    FakeConnection.calls.clear()
    FakeConnection.response = Response(
        payload={
            "code": "0",
            "data": [instrument(instId=real_public_shape)],
        }
    )
    monkeypatch.setattr(mod, "HTTPSConnection", FakeConnection)
    asyncio.run(
        mod.require_qualified_isolated_xperp(
            region="eea",
            inst_id=real_public_shape,
            inst_type="FUTURES",
            settle="USD",
        )
    )
    assert FakeConnection.calls == [
        (
            "eea.okx.com",
            "GET",
            "/api/v5/public/instruments?instType=FUTURES&instId=BTC-USD_UM_XPERP-310404",
        )
    ]


@pytest.mark.parametrize(
    "unsafe",
    [
        "BTC/USD_UM_XPERP-310404",
        "BTC-USD_UM_XPERP-310404?instType=SWAP",
        "BTC-USD_UM_XPERP-310404%0D",
        "BTC-USD_UM_XPERP-310404&foo=x",
    ],
)
def test_xperp_instrument_parameter_cannot_escape_url_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    FakeConnection.calls.clear()
    monkeypatch.setattr(mod, "HTTPSConnection", FakeConnection)
    with pytest.raises(mod.OkxInstrumentPreflightError):
        asyncio.run(
            mod.require_qualified_isolated_xperp(
                region="eea",
                inst_id=unsafe,
                inst_type="FUTURES",
                settle="USD",
            )
        )
    assert FakeConnection.calls == []
