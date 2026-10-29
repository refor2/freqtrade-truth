"""Account-independent, region-pinned OKX XPERP instrument preflight.

Exact public GET only: /api/v5/public/instruments. No redirects, no secrets,
no account request and no claims about account-specific permissions.
"""

from __future__ import annotations

import asyncio
import json
from http.client import HTTPException, HTTPSConnection
from typing import Any, cast
from urllib.parse import urlencode

_OFFICIAL_REGIONAL_HOSTS = {
    "eea": "eea.okx.com",
    "global": "www.okx.com",
    "us": "us.okx.com",
}
_MAX_RESPONSE_BYTES = 128 * 1024


class OkxInstrumentPreflightError(RuntimeError):
    """The instrument metadata is not proven for this requested source mode."""


async def require_qualified_isolated_xperp(
    *,
    region: str,
    inst_id: str,
    inst_type: str,
    settle: str,
) -> None:
    """Reject missing or mismatching public metadata BEFORE any account GET."""
    return await asyncio.to_thread(
        _validate_sync,
        region=region,
        inst_id=inst_id,
        inst_type=inst_type,
        settle=settle,
    )


def _validate_sync(*, region: str, inst_id: str, inst_type: str, settle: str) -> None:
    host = _OFFICIAL_REGIONAL_HOSTS.get(region)
    if host is None:
        raise OkxInstrumentPreflightError("unsupported OKX region")
    if inst_type != "FUTURES" or settle != "USD":
        raise OkxInstrumentPreflightError("XPERP instType/settle preflight contract mismatch")
    if not inst_id or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in inst_id):
        raise OkxInstrumentPreflightError("invalid native instrument ID")
    target = "/api/v5/public/instruments?" + urlencode(
        (("instType", inst_type), ("instId", inst_id)),
    )
    connection = HTTPSConnection(host, None, timeout=8.0)
    response: Any = None
    try:
        connection.request(
            "GET",
            target,
            headers={"Accept": "application/json", "User-Agent": "freqtrade-truth/0.1.0"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise OkxInstrumentPreflightError("public instrument HTTP response not 200")
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise OkxInstrumentPreflightError("public instrument response too large")
    except OkxInstrumentPreflightError:
        raise
    except (OSError, HTTPException, TimeoutError) as exc:
        raise OkxInstrumentPreflightError("public instrument read unavailable") from exc
    finally:
        if response is not None:
            response.close()
        connection.close()

    try:
        doc: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OkxInstrumentPreflightError("invalid public instrument JSON") from exc
    if not isinstance(doc, dict) or doc.get("code") != "0":
        raise OkxInstrumentPreflightError("public instrument API code invalid")
    rows = doc.get("data")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise OkxInstrumentPreflightError("missing or ambiguous public instrument metadata")
    info = cast(dict[str, Any], rows[0])
    expected = {
        "instType": inst_type,
        "instId": inst_id,
        "ruleType": "xperp",
        "ctType": "linear",
        "settleCcy": settle,
        "state": "live",
    }
    for field, value in expected.items():
        if info.get(field) != value:
            raise OkxInstrumentPreflightError(f"public instrument {field} mismatch")
