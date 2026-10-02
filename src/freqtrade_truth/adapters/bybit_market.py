"""Public Bybit V5 market discovery and inverse-product preflight."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from http.client import HTTPException, HTTPResponse, HTTPSConnection
from typing import cast
from urllib.parse import urlencode, urlsplit

from freqtrade_truth.adapters.http import (
    HttpTransportError,
    JsonHttpTransport,
    JsonObject,
    JsonValue,
)

_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class BybitMarketPreflightError(RuntimeError):
    """Raised when a required public Bybit market invariant is not satisfied."""


@dataclass(frozen=True, slots=True)
class BybitInverseMarketInfo:
    """Verified public specification for one tradable inverse perpetual."""

    symbol: str
    base_coin: str
    quote_coin: str
    settle_coin: str
    contract_type: str
    status: str
    min_leverage: Decimal
    max_leverage: Decimal
    leverage_step: Decimal
    min_order_qty: Decimal
    qty_step: Decimal
    tick_size: Decimal


class BybitV5PublicTransport:
    """Small GET-only HTTPS transport for public Bybit V5 endpoints."""

    def __init__(
        self,
        base_url: str = "https://api.bybit.com",
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        parsed = urlsplit(base_url)

        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials must not be embedded in base_url")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain query parameters or fragments")
        if parsed.hostname is None:
            raise ValueError("base_url must contain a hostname")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")

        self._hostname = parsed.hostname
        self._port = parsed.port
        self._base_path = parsed.path.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        return await asyncio.to_thread(self._get_json_sync, path, params)

    def _get_json_sync(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None,
    ) -> JsonObject:
        clean_path = _validate_v5_path(path)
        target = f"{self._base_path}/{clean_path}"
        if not target.startswith("/"):
            target = f"/{target}"
        if params:
            target = f"{target}?{urlencode(params)}"

        connection = HTTPSConnection(
            self._hostname,
            self._port,
            timeout=self._timeout_seconds,
        )
        response: HTTPResponse | None = None

        try:
            connection.request("GET", target, headers={"Accept": "application/json"})
            response = connection.getresponse()

            if response.status < 200 or response.status >= 300:
                raise HttpTransportError(
                    f"HTTP request failed with status {response.status}",
                    status_code=response.status,
                )

            raw_body = response.read(self._max_response_bytes + 1)
        except HttpTransportError:
            raise
        except (HTTPException, TimeoutError, OSError) as exc:
            raise HttpTransportError("HTTP request failed") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()

        if len(raw_body) > self._max_response_bytes:
            raise HttpTransportError("HTTP response exceeded configured size limit")

        try:
            decoded_body = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HttpTransportError("HTTP response was not valid UTF-8") from exc

        try:
            payload = json.loads(decoded_body, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise HttpTransportError("HTTP response was not valid JSON") from exc

        if not isinstance(payload, dict):
            raise HttpTransportError("HTTP response must be a JSON object")

        return cast(JsonObject, payload)


async def verify_bybit_inverse_perpetual(
    transport: JsonHttpTransport,
    *,
    symbol: str,
    expected_base_coin: str,
    expected_quote_coin: str,
    expected_settle_coin: str,
) -> BybitInverseMarketInfo:
    """Verify that one Bybit inverse perpetual is publicly tradable as expected."""

    _require_uppercase_token(symbol, "symbol")
    _require_uppercase_token(expected_base_coin, "expected_base_coin")
    _require_uppercase_token(expected_quote_coin, "expected_quote_coin")
    _require_uppercase_token(expected_settle_coin, "expected_settle_coin")

    payload = await transport.get_json(
        "v5/market/instruments-info",
        {
            "category": "inverse",
            "symbol": symbol,
            "status": "Trading",
        },
    )
    result = _require_success_result(payload)

    category = _require_non_empty_str(result, "category")
    if category != "inverse":
        raise BybitMarketPreflightError("Bybit market category must be 'inverse'")

    items = _require_object_list(result, "list")
    if len(items) != 1:
        raise BybitMarketPreflightError(
            "Bybit market preflight requires exactly one matching instrument"
        )

    item = items[0]
    returned_symbol = _require_non_empty_str(item, "symbol")
    contract_type = _require_non_empty_str(item, "contractType")
    status = _require_non_empty_str(item, "status")
    base_coin = _require_non_empty_str(item, "baseCoin")
    quote_coin = _require_non_empty_str(item, "quoteCoin")
    settle_coin = _require_non_empty_str(item, "settleCoin")

    if returned_symbol != symbol:
        raise BybitMarketPreflightError("Bybit market symbol does not match requested symbol")
    if contract_type != "InversePerpetual":
        raise BybitMarketPreflightError("Bybit market contract must be InversePerpetual")
    if status != "Trading":
        raise BybitMarketPreflightError("Bybit market instrument must be Trading")
    if base_coin != expected_base_coin:
        raise BybitMarketPreflightError("Bybit market base coin does not match expectation")
    if quote_coin != expected_quote_coin:
        raise BybitMarketPreflightError("Bybit market quote coin does not match expectation")
    if settle_coin != expected_settle_coin:
        raise BybitMarketPreflightError("Bybit market settle coin does not match expectation")

    leverage = _require_object(item, "leverageFilter")
    lot_size = _require_object(item, "lotSizeFilter")
    price = _require_object(item, "priceFilter")

    return BybitInverseMarketInfo(
        symbol=returned_symbol,
        base_coin=base_coin,
        quote_coin=quote_coin,
        settle_coin=settle_coin,
        contract_type=contract_type,
        status=status,
        min_leverage=_require_decimal_string(leverage, "minLeverage"),
        max_leverage=_require_decimal_string(leverage, "maxLeverage"),
        leverage_step=_require_decimal_string(leverage, "leverageStep"),
        min_order_qty=_require_decimal_string(lot_size, "minOrderQty"),
        qty_step=_require_decimal_string(lot_size, "qtyStep"),
        tick_size=_require_decimal_string(price, "tickSize"),
    )


async def list_bybit_inverse_trading_symbols(
    transport: JsonHttpTransport,
) -> tuple[str, ...]:
    """Return all currently Trading inverse symbols using public cursor pagination."""

    symbols: list[str] = []
    seen_symbols: set[str] = set()
    seen_cursors: set[str] = set()
    cursor: str | None = None

    while True:
        params: dict[str, str | int | bool] = {
            "category": "inverse",
            "status": "Trading",
            "limit": 1000,
        }
        if cursor is not None:
            params["cursor"] = cursor

        payload = await transport.get_json("v5/market/instruments-info", params)
        result = _require_success_result(payload)

        if _require_non_empty_str(result, "category") != "inverse":
            raise BybitMarketPreflightError("Bybit market category must be 'inverse'")

        for item in _require_object_list(result, "list"):
            symbol = _require_non_empty_str(item, "symbol")
            if symbol in seen_symbols:
                raise BybitMarketPreflightError("Bybit returned a duplicate inverse symbol")
            if _require_non_empty_str(item, "status") != "Trading":
                raise BybitMarketPreflightError("Bybit returned a non-Trading inverse symbol")
            seen_symbols.add(symbol)
            symbols.append(symbol)

        next_cursor = _require_cursor(result)
        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            raise BybitMarketPreflightError("Bybit market pagination cursor repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return tuple(sorted(symbols))


def _validate_v5_path(path: str) -> str:
    clean_path = path.lstrip("/")
    if not clean_path or "?" in clean_path or "#" in clean_path:
        raise ValueError("path must be a non-empty API path without query or fragment")
    if any(segment == ".." for segment in clean_path.split("/")):
        raise ValueError("path traversal is not allowed")
    if not clean_path.startswith("v5/"):
        raise ValueError("Bybit public transport only permits v5 API paths")
    return clean_path


def _require_uppercase_token(value: str, field_name: str) -> None:
    if not value or value != value.upper():
        raise ValueError(f"{field_name} must be a non-empty uppercase value")


def _require_success_result(payload: JsonObject) -> Mapping[str, JsonValue]:
    ret_code = payload.get("retCode")
    if isinstance(ret_code, bool) or not isinstance(ret_code, int):
        raise BybitMarketPreflightError("Bybit response field 'retCode' must be an int")
    if ret_code != 0:
        raise BybitMarketPreflightError(f"Bybit API returned retCode {ret_code}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise BybitMarketPreflightError("Bybit response field 'result' must be an object")
    return result


def _require_object(
    payload: Mapping[str, JsonValue],
    key: str,
) -> Mapping[str, JsonValue]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise BybitMarketPreflightError(f"Bybit response field '{key}' must be an object")
    return value


def _require_object_list(
    payload: Mapping[str, JsonValue],
    key: str,
) -> list[Mapping[str, JsonValue]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise BybitMarketPreflightError(f"Bybit response field '{key}' must be a list")

    items: list[Mapping[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            raise BybitMarketPreflightError(
                f"Bybit response field '{key}' must contain objects"
            )
        items.append(item)
    return items


def _require_non_empty_str(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BybitMarketPreflightError(
            f"Bybit response field '{key}' must be a non-empty string"
        )
    return value


def _require_decimal_string(
    payload: Mapping[str, JsonValue],
    key: str,
) -> Decimal:
    value = _require_non_empty_str(payload, key)
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise BybitMarketPreflightError(
            f"Bybit response field '{key}' must be decimal"
        ) from exc
    if not result.is_finite():
        raise BybitMarketPreflightError(f"Bybit response field '{key}' must be finite")
    return result


def _require_cursor(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("nextPageCursor")
    if not isinstance(value, str):
        raise BybitMarketPreflightError(
            "Bybit response field 'nextPageCursor' must be a string"
        )
    return value
