from freqtrade_truth.adapters import (
    BybitApiKeySafetyError,
    BybitInverseClosedPnlAdapter,
    BybitInverseTransactionLogReader,
    BybitResponseError,
    BybitTransactionLogEntry,
    BybitTransactionLogError,
    BybitTransactionLogQuery,
    BybitV5HmacTransport,
    FreqtradeReadAdapter,
    FreqtradeResponseError,
    HttpTransportError,
    JsonHttpTransport,
    ReadOnlyTradeAdapter,
    RetryingJsonTransport,
    StdlibJsonTransport,
    verify_bybit_read_only_key,
)


def test_preview_public_adapter_api_is_importable() -> None:
    exported = {
        BybitApiKeySafetyError,
        BybitInverseClosedPnlAdapter,
        BybitInverseTransactionLogReader,
        BybitResponseError,
        BybitTransactionLogEntry,
        BybitTransactionLogError,
        BybitTransactionLogQuery,
        BybitV5HmacTransport,
        FreqtradeReadAdapter,
        FreqtradeResponseError,
        HttpTransportError,
        JsonHttpTransport,
        ReadOnlyTradeAdapter,
        RetryingJsonTransport,
        StdlibJsonTransport,
        verify_bybit_read_only_key,
    }

    assert len(exported) == 16
