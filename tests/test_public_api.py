import freqtrade_truth.core as core
from freqtrade_truth.adapters import (
    BybitApiKeySafetyError,
    BybitInverseClosedPnlAdapter,
    BybitInverseMarketInfo,
    BybitInverseTransactionLogReader,
    BybitMarketPreflightError,
    BybitResponseError,
    BybitTransactionLogEntry,
    BybitTransactionLogError,
    BybitTransactionLogQuery,
    BybitV5HmacTransport,
    BybitV5PublicTransport,
    FreqtradeReadAdapter,
    FreqtradeResponseError,
    HttpTransportError,
    JsonHttpTransport,
    ReadOnlyTradeAdapter,
    RetryingJsonTransport,
    StdlibJsonTransport,
    list_bybit_inverse_trading_symbols,
    verify_bybit_inverse_perpetual,
    verify_bybit_read_only_key,
)


def test_preview_public_adapter_api_is_importable() -> None:
    exported = {
        BybitApiKeySafetyError,
        BybitInverseClosedPnlAdapter,
        BybitInverseMarketInfo,
        BybitInverseTransactionLogReader,
        BybitMarketPreflightError,
        BybitResponseError,
        BybitTransactionLogEntry,
        BybitTransactionLogError,
        BybitTransactionLogQuery,
        BybitV5HmacTransport,
        BybitV5PublicTransport,
        FreqtradeReadAdapter,
        FreqtradeResponseError,
        HttpTransportError,
        JsonHttpTransport,
        ReadOnlyTradeAdapter,
        RetryingJsonTransport,
        StdlibJsonTransport,
        list_bybit_inverse_trading_symbols,
        verify_bybit_inverse_perpetual,
        verify_bybit_read_only_key,
    }

    assert len(exported) == 21


def test_preview_public_core_api_exports_are_unique_and_resolvable() -> None:
    assert core.__all__
    assert len(core.__all__) == len(set(core.__all__))
    assert all(hasattr(core, name) for name in core.__all__)
