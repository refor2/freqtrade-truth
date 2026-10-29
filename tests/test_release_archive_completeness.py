"""The public source archive must be able to rerun its own tests."""

from __future__ import annotations

import io
import runpy
import tarfile
from pathlib import Path

_CHECKER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "release_integrity_check.py")
)


def test_source_archive_cannot_pass_without_supporting_scripts_and_fixture(
    tmp_path: Path,
) -> None:
    version = "0.1.0"
    root = f"freqtrade_truth-{version}/"
    sdist = tmp_path / f"freqtrade_truth-{version}.tar.gz"
    source_files = (
        "examples/candidate-report.synthetic.json",
        "docs/candidate-reporting.md",
        "examples/okx-native.synthetic.json",
        "docs/okx-native-verification.md",
    )
    with tarfile.open(sdist, "w:gz") as archive:
        for name in source_files:
            contents = b"synthetic public file\n"
            info = tarfile.TarInfo(root + name)
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))

    findings = _CHECKER["_sdist_findings"](sdist, version)
    assert any("scripts/public_safety_check.py" in line for line in findings)
    assert any("tests/fixtures/normalized_trades.json" in line for line in findings)
    assert any(".githooks/pre-push" in line for line in findings)
