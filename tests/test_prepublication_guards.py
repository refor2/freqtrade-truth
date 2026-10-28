"""Pre-publication scanning must stop secrets *before* GitHub sees commits."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SAMPLE = "api" + '_key = "synthetic-prepublication-do-not-use"\n'


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "scripts").mkdir()
    for name in ("prepublication_guard.py", "public_safety_check.py"):
        shutil.copy2(SCRIPTS / name, root / "scripts" / name)
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    git(root, "init", "-q")
    git(root, "config", "user.name", "Synthetic Test")
    git(root, "config", "user.email", "synthetic@example.invalid")
    return root


def run_guard(root: Path, mode: str, *, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/prepublication_guard.py", mode],
        cwd=root,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def commit(root: Path, message: str) -> str:
    git(root, "add", ".")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def test_staged_guard_scans_index_blob_not_modified_worktree(tmp_path: Path) -> None:
    root = repo(tmp_path / "staged")
    candidate = root / "sample.csv"
    candidate.write_text(SAMPLE)
    git(root, "add", "sample.csv")
    candidate.write_text("cleaned locally but not staged\n")
    outcome = run_guard(root, "--staged")
    assert outcome.returncode != 0
    assert "possible assigned secret" in outcome.stdout
    assert "synthetic-prepublication-do-not-use" not in outcome.stdout


def test_prepush_rejects_secret_in_intermediate_commit_even_if_head_clean(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path / "history")
    path = root / "fixture.jsonl"
    path.write_text('view = "clean"\n')
    base = commit(root, "initial clean")
    path.write_text(SAMPLE)
    commit(root, "unsafe intermediate")
    path.write_text('view = "clean again"\n')
    head = commit(root, "clean latest")
    outcome = run_guard(
        root, "--pre-push", stdin=f"refs/heads/test {head} refs/heads/test {base}\n"
    )
    assert outcome.returncode != 0
    assert "possible assigned secret" in outcome.stdout
    assert "synthetic-prepublication-do-not-use" not in outcome.stdout


def test_prepush_new_branch_checks_root_history(tmp_path: Path) -> None:
    root = repo(tmp_path / "newbranch")
    (root / "source.txt").write_text(SAMPLE)
    head = commit(root, "initial unsafe")
    outcome = run_guard(
        root,
        "--pre-push",
        stdin=f"refs/heads/branch {head} refs/heads/branch {'0' * 40}\n",
    )
    assert outcome.returncode != 0
    assert "possible assigned secret" in outcome.stdout


def test_staged_guard_rejects_unknown_binary_and_private_names(tmp_path: Path) -> None:
    root = repo(tmp_path / "binary")
    (root / "opaque.bin").write_bytes(b"\x00\xff\x80private")
    (root / ".env.production").write_text("safe placeholder\n")
    git(root, "add", "-f", "opaque.bin", ".env.production")
    outcome = run_guard(root, "--staged")
    assert outcome.returncode != 0
    assert "unscannable binary content" in outcome.stdout
    assert "prohibited tracked filename" in outcome.stdout


def test_benign_staged_and_prepush_are_allowed(tmp_path: Path) -> None:
    root = repo(tmp_path / "clean")
    (root / "source.py").write_text('message = "synthetic public"\n')
    git(root, "add", ".")
    assert run_guard(root, "--staged").returncode == 0
    head = commit(root, "safe initial")
    outcome = run_guard(
        root,
        "--pre-push",
        stdin=f"refs/heads/test {head} refs/heads/test {'0' * 40}\n",
    )
    assert outcome.returncode == 0


def test_pre_push_rejects_malformed_ref_input(tmp_path: Path) -> None:
    root = repo(tmp_path / "badline")
    outcome = run_guard(root, "--pre-push", stdin="malformed\n")
    assert outcome.returncode != 0
    assert "BLOCKED" in outcome.stdout


def test_staged_guard_rejects_bare_exchange_secret_even_in_csv(tmp_path: Path) -> None:
    root = repo(tmp_path / "bare-secret")
    payload = "BYBIT_" + "API_KEY=" + "synthetic-unquoted-access-string-000000000000"
    (root / "settings.csv").write_text(payload)
    git(root, "add", "settings.csv")
    outcome = run_guard(root, "--staged")
    assert outcome.returncode != 0
    assert "possible unquoted secret assignment" in outcome.stdout
    assert "synthetic-unquoted-access-string" not in outcome.stdout
