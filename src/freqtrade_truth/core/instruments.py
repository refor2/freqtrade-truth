"""Canonical public instrument identifiers."""

from __future__ import annotations


def canonical_derivative_instrument(
    base_currency: str,
    quote_currency: str,
    settlement_currency: str,
) -> str:
    """Return the canonical ``BASE/QUOTE:SETTLEMENT`` derivative label."""

    base = _require_asset_token(base_currency, "base_currency")
    quote = _require_asset_token(quote_currency, "quote_currency")
    settlement = _require_asset_token(settlement_currency, "settlement_currency")
    return f"{base}/{quote}:{settlement}"


def _require_asset_token(value: str, field_name: str) -> str:
    if not value or value != value.strip() or value != value.upper():
        raise ValueError(f"{field_name} must be a non-empty uppercase asset token")
    if "/" in value or ":" in value:
        raise ValueError(f"{field_name} must not contain instrument delimiters")
    return value
