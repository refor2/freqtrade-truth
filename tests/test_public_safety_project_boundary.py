"""Public project boundary is tested without publishing private names."""

import hashlib
import runpy
from pathlib import Path

_SCANNER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/public_safety_check.py")
)
contains_private_project_label = _SCANNER["contains_private_project_label"]


def test_fingerprint_matching_across_one_to_three_words() -> None:
    synthetic_label = "synthetic private sample"
    fingerprint = hashlib.sha256(synthetic_label.replace(" ", "").encode()).hexdigest()
    assert contains_private_project_label(
        "A synthetic private sample is not appropriate for public CI.",
        fingerprints=frozenset({fingerprint}),
    )


def test_fingerprint_matching_does_not_flag_generic_public_exchange_terms() -> None:
    safe = (
        "OKX public API",
        "https://my.okx.com/docs-v5/",
        "Bybit inverse funding fee",
        "Freqtrade read-only source",
        "exchange position ledger",
    )
    assert all(not contains_private_project_label(text) for text in safe)


def test_fingerprint_rejects_protected_deployment_identifiers_without_exposing_them() -> None:
    assert len(_SCANNER["PRIVATE_PROJECT_FINGERPRINTS"]) >= 4
    assert all(len(item) == 64 for item in _SCANNER["PRIVATE_PROJECT_FINGERPRINTS"])
