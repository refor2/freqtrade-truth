"""Offline read-only event audit CLI uses redacted summaries only."""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path

from freqtrade_truth.cli import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "settlement-ledger.synthetic.json"


def test_cli_settlement_audit_from_local_json(tmp_path: Path) -> None:
    file = tmp_path / 'synthetic.json'
    file.write_bytes(EXAMPLE.read_bytes())
    stdout, stderr = io.StringIO(), io.StringIO()
    status = main(['settlement-audit', '--input', str(file), '--tolerance', '0'], stdout=stdout, stderr=stderr)
    assert status == 0
    assert stderr.getvalue() == ''
    report = json.loads(stdout.getvalue(), parse_float=Decimal)
    assert report['numeric_status'] == 'match'
    assert report['verified_financial_truth'] is False
    assert 'synthetic-position' not in stdout.getvalue()


def test_invalid_json_and_duplicate_property_are_redacted(tmp_path: Path) -> None:
    file = tmp_path / 'broken.json'
    file.write_text('{"schema":"x","schema":"y","secret":"do-not-echo"}', encoding='utf-8')
    stdout, stderr = io.StringIO(), io.StringIO()
    status = main(['settlement-audit', '--input', str(file)], stdout=stdout, stderr=stderr)
    assert status == 2
    assert stdout.getvalue() == ''
    assert 'do-not-echo' not in stderr.getvalue()


def test_symlink_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / 'real.json'
    real.write_bytes(EXAMPLE.read_bytes())
    link = tmp_path / 'alias.json'
    link.symlink_to(real)
    assert main(['settlement-audit', '--input', str(link)], stdout=io.StringIO(), stderr=io.StringIO()) == 2
