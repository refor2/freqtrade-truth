"""Fail-closed preflight for Bybit API key safety."""

from __future__ import annotations

from collections.abc import Mapping

from freqtrade_truth.adapters.http import JsonHttpTransport, JsonObject, JsonValue


class BybitApiKeySafetyError(RuntimeError):
    """Raised when a Bybit API key cannot be verified as read-only."""


async def verify_bybit_read_only_key(transport: JsonHttpTransport) -> None:
    """Verify that the authenticated Bybit API key is read-only.

    The Bybit V5 key-information endpoint reports readOnly=1 for read-only
    keys and readOnly=0 for read/write keys. Any malformed, unsuccessful, or
    non-read-only response fails closed.
    """

    payload = await transport.get_json("v5/user/query-api")
    result = _require_success_result(payload)

    read_only = result.get("readOnly")
    if isinstance(read_only, bool) or not isinstance(read_only, int):
        raise BybitApiKeySafetyError("Bybit API key readOnly flag must be an int")

    if read_only != 1:
        raise BybitApiKeySafetyError("Bybit API key must be configured as read-only")


def _require_success_result(payload: JsonObject) -> Mapping[str, JsonValue]:
    ret_code = payload.get("retCode")
    if isinstance(ret_code, bool) or not isinstance(ret_code, int):
        raise BybitApiKeySafetyError("Bybit response field 'retCode' must be an int")

    if ret_code != 0:
        raise BybitApiKeySafetyError(f"Bybit API returned retCode {ret_code}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise BybitApiKeySafetyError("Bybit response field 'result' must be an object")

    return result
