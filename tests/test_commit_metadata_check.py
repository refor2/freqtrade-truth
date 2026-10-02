import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_SOURCE = (
    Path(__file__).resolve().parents[1] / "scripts" / "commit_metadata_check.py"
)


def run_privacy_check(
    tmp_path: Path,
    *,
    author_name: str,
    author_email: str,
) -> subprocess.CompletedProcess[str]:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(SCRIPT_SOURCE, scripts_dir / "commit_metadata_check.py")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", author_name], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", author_email], cwd=tmp_path, check=True)

    (tmp_path / "fixture.txt").write_text("synthetic\n", encoding="utf-8")
    subprocess.run(["git", "add", "fixture.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Synthetic fixture"], cwd=tmp_path, check=True)

    return subprocess.run(
        [sys.executable, "scripts/commit_metadata_check.py"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )


def test_protected_identity_with_github_noreply_passes(tmp_path: Path) -> None:
    result = run_privacy_check(
        tmp_path,
        author_name="refor2",
        author_email="12345+refor2@users.noreply.github.com",
    )

    assert result.returncode == 0
    assert "PASSED" in result.stdout


def test_protected_identity_with_regular_email_fails(tmp_path: Path) -> None:
    result = run_privacy_check(
        tmp_path,
        author_name="refor2",
        author_email="private@example.invalid",
    )

    assert result.returncode == 1
    assert "FAILED" in result.stdout


def test_external_contributor_with_regular_email_is_not_blocked(tmp_path: Path) -> None:
    result = run_privacy_check(
        tmp_path,
        author_name="external-contributor",
        author_email="public@example.invalid",
    )

    assert result.returncode == 0
