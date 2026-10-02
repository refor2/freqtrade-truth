"""Read-only integration adapter contracts."""

from freqtrade_truth.adapters.base import ReadOnlyTradeAdapter
from freqtrade_truth.adapters.bybit import (
    BybitInverseClosedPnlAdapter,
    BybitResponseError,
)
from freqtrade_truth.adapters.bybit_auth import BybitV5HmacTransport
from freqtrade_truth.adapters.bybit_preflight import (
    BybitApiKeySafetyError,
    verify_bybit_read_only_key,
)
from freqtrade_truth.adapters.freqtrade import (
    FreqtradeReadAdapter,
    FreqtradeResponseError,
)
from freqtrade_truth.adapters.http import (
    HttpTransportError,
    JsonHttpTransport,
    RetryingJsonTransport,
    StdlibJsonTransport,
)

__all__ = [
    "BybitInverseClosedPnlAdapter",
    "BybitResponseError",
    "BybitV5HmacTransport",
    "BybitApiKeySafetyError",
    "FreqtradeReadAdapter",
    "FreqtradeResponseError",
    "HttpTransportError",
    "JsonHttpTransport",
    "ReadOnlyTradeAdapter",
    "RetryingJsonTransport",
    "StdlibJsonTransport",
    "verify_bybit_read_only_key",
]
