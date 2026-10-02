# Compatibility

## Python

Freqtrade Truth is continuously tested on:

- Python 3.11
- Python 3.12
- Python 3.13
- Python 3.14

A change is not considered compatible until the full CI matrix passes.

## Freqtrade

The initial Freqtrade adapter uses the documented REST endpoint:

- `GET /api/v1/trades`
- `limit`
- `offset`
- `order_by_id=false`

The implementation was reviewed against the public Freqtrade stable REST documentation and a public Freqtrade source snapshot on 2026-10-02.

Reference source snapshot:

- https://github.com/freqtrade/freqtrade/tree/621a44cef1e275e12f58a6d75e336a0fbb0f1c3b

Relevant upstream schema fields currently consumed:

- `trade_id`
- `pair`
- `quote_currency`
- `is_open`
- `open_timestamp`
- `close_timestamp`
- `profit_abs`
- `funding_fees`

No minimum Freqtrade release number is claimed yet.

If an upstream version changes these fields or their semantics, the adapter must fail closed rather than guess.

## Exchange adapters

No production exchange settlement adapter is part of the 0.0.1 preview.

Future exchange adapters must publish:

- the public API documentation used as their source,
- the exact normalized field mapping,
- known semantic limitations,
- contract tests using synthetic fixtures.

## Operating systems

The library itself is platform-neutral Python.

CI currently validates Linux runners. Windows and macOS are expected to work but are not yet part of the release compatibility guarantee.

## Stability

Version 0.0.x is pre-alpha.

Public contracts are intentionally small but may still change before 0.1.0. Breaking changes must be documented in the changelog.
