import asyncio
from collections.abc import Mapping

import pytest

from freqtrade_truth.adapters.http import (
    HttpTransportError,
    JsonObject,
    RetryingJsonTransport,
)


class FlakyTransport:
    def __init__(
        self,
        failures: list[HttpTransportError],
        payload: JsonObject | None = None,
    ) -> None:
        self.failures = list(failures)
        self.payload = payload or {"ok": True}
        self.calls = 0

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        del path, params
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.payload


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def test_retry_transport_respects_retry_after_for_429() -> None:
    transport = FlakyTransport(
        [
            HttpTransportError(
                "rate limited",
                status_code=429,
                retry_after_seconds=2.0,
            )
        ]
    )
    sleep = SleepRecorder()
    retrying = RetryingJsonTransport(
        transport,
        max_attempts=3,
        max_delay_seconds=5.0,
        sleep=sleep,
    )

    payload = asyncio.run(retrying.get_json("trades"))

    assert payload == {"ok": True}
    assert transport.calls == 2
    assert sleep.delays == [2.0]


def test_retry_transport_uses_bounded_exponential_backoff_for_503() -> None:
    transport = FlakyTransport(
        [
            HttpTransportError("busy", status_code=503),
            HttpTransportError("still busy", status_code=503),
        ]
    )
    sleep = SleepRecorder()
    retrying = RetryingJsonTransport(
        transport,
        max_attempts=4,
        base_delay_seconds=0.5,
        max_delay_seconds=0.75,
        sleep=sleep,
    )

    asyncio.run(retrying.get_json("trades"))

    assert transport.calls == 3
    assert sleep.delays == [0.5, 0.75]


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 500])
def test_retry_transport_does_not_retry_non_configured_statuses(status_code: int) -> None:
    transport = FlakyTransport([HttpTransportError("request failed", status_code=status_code)])
    sleep = SleepRecorder()
    retrying = RetryingJsonTransport(transport, sleep=sleep)

    with pytest.raises(HttpTransportError):
        asyncio.run(retrying.get_json("trades"))

    assert transport.calls == 1
    assert sleep.delays == []


def test_retry_transport_stops_at_max_attempts() -> None:
    transport = FlakyTransport(
        [
            HttpTransportError("busy", status_code=503),
            HttpTransportError("busy", status_code=503),
            HttpTransportError("busy", status_code=503),
        ]
    )
    sleep = SleepRecorder()
    retrying = RetryingJsonTransport(
        transport,
        max_attempts=3,
        base_delay_seconds=0.1,
        max_delay_seconds=1.0,
        sleep=sleep,
    )

    with pytest.raises(HttpTransportError):
        asyncio.run(retrying.get_json("trades"))

    assert transport.calls == 3
    assert sleep.delays == [0.1, 0.2]


def test_retry_after_is_capped_by_max_delay() -> None:
    transport = FlakyTransport(
        [
            HttpTransportError(
                "rate limited",
                status_code=429,
                retry_after_seconds=120.0,
            )
        ]
    )
    sleep = SleepRecorder()
    retrying = RetryingJsonTransport(
        transport,
        max_delay_seconds=3.0,
        sleep=sleep,
    )

    asyncio.run(retrying.get_json("trades"))

    assert sleep.delays == [3.0]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_attempts": 0}, "max_attempts"),
        ({"base_delay_seconds": -1.0}, "base_delay_seconds"),
        ({"max_delay_seconds": -1.0}, "max_delay_seconds"),
        (
            {"base_delay_seconds": 2.0, "max_delay_seconds": 1.0},
            "must not exceed",
        ),
        ({"retryable_status_codes": frozenset()}, "must not be empty"),
        ({"retryable_status_codes": frozenset({99})}, "valid HTTP"),
    ],
)
def test_retry_transport_validates_policy(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RetryingJsonTransport(FlakyTransport([]), **kwargs)  # type: ignore[arg-type]
