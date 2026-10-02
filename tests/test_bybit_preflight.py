import asyncio
from collections.abc import Mapping

import pytest

from freqtrade_truth.adapters.bybit_preflight import (
    BybitApiKeySafetyError,
    verify_bybit_read_only_key,
)
from freqtrade_truth.adapters.http import JsonObject


class StaticTransport:
    def __init__(self, payload: JsonObject) -> None:
        self._payload = payload
        self.requests: list[tuple[str, Mapping[str, str | int | bool] | None]] = []

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        self.requests.append((path, params))
        return self._payload


def response(*, read_only: object = 1, ret_code: object = 0) -> JsonObject:
    return {
        "retCode": ret_code,
        "retMsg": "OK",
        "result": {
            "readOnly": read_only,
            "secret": "",
            "permissions": {
                "ContractTrade": ["Position"],
            },
        },
        "time": 0,
    }


def test_read_only_key_passes_preflight() -> None:
    transport = StaticTransport(response(read_only=1))

    asyncio.run(verify_bybit_read_only_key(transport))

    assert transport.requests == [("v5/user/query-api", None)]


def test_read_write_key_fails_preflight() -> None:
    transport = StaticTransport(response(read_only=0))

    with pytest.raises(BybitApiKeySafetyError, match="read-only"):
        asyncio.run(verify_bybit_read_only_key(transport))


@pytest.mark.parametrize("read_only", [None, True, "1", 2])
def test_invalid_read_only_flag_fails_closed(read_only: object) -> None:
    transport = StaticTransport(response(read_only=read_only))

    with pytest.raises(BybitApiKeySafetyError):
        asyncio.run(verify_bybit_read_only_key(transport))


@pytest.mark.parametrize("ret_code", [True, "0", 10001])
def test_invalid_or_unsuccessful_response_fails_closed(ret_code: object) -> None:
    transport = StaticTransport(response(ret_code=ret_code))

    with pytest.raises(BybitApiKeySafetyError):
        asyncio.run(verify_bybit_read_only_key(transport))


def test_missing_result_object_fails_closed() -> None:
    transport = StaticTransport(
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": None,
            "time": 0,
        }
    )

    with pytest.raises(BybitApiKeySafetyError, match="result"):
        asyncio.run(verify_bybit_read_only_key(transport))
