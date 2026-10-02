from freqtrade_truth.adapters import (
    BybitInverseClosedPnlAdapter,
    BybitResponseError,
    BybitV5HmacTransport,
    FreqtradeReadAdapter,
    FreqtradeResponseError,
    HttpTransportError,
    JsonHttpTransport,
    ReadOnlyTradeAdapter,
    RetryingJsonTransport,
    StdlibJsonTransport,
)


def test_preview_public_adapter_api_is_importable() -> None:
    exported = {
        BybitInverseClosedPnlAdapter,
        BybitResponseError,
        BybitV5HmacTransport,
        FreqtradeReadAdapter,
        FreqtradeResponseError,
        HttpTransportError,
        JsonHttpTransport,
        ReadOnlyTradeAdapter,
        RetryingJsonTransport,
        StdlibJsonTransport,
    }

    assert len(exported) == 10
