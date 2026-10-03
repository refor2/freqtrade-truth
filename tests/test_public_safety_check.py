import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "public_safety_check.py"


def run_safety_check(
    tmp_path: Path,
    files: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(SCRIPT_SOURCE, scripts_dir / "public_safety_check.py")

    for relative_path, body in files.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

    return subprocess.run(
        [sys.executable, "scripts/public_safety_check.py"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )


def test_clean_synthetic_public_content_passes(tmp_path: Path) -> None:
    result = run_safety_check(
        tmp_path,
        {
            ".env.example": "API_KEY=<placeholder>\n",
            "docs/example.md": "Synthetic endpoint example uses 8.8.8.8 only.\n",
            "config.toml": 'name = "synthetic"\n',
        },
    )

    assert result.returncode == 0
    assert "PASSED" in result.stdout


def test_tracked_environment_variant_fails_even_with_placeholder_content(
    tmp_path: Path,
) -> None:
    result = run_safety_check(
        tmp_path,
        {".env.production": "API_KEY=<placeholder>\n"},
    )

    assert result.returncode == 1
    assert "prohibited tracked filename" in result.stdout


def test_assigned_secret_fails(tmp_path: Path) -> None:
    result = run_safety_check(
        tmp_path,
        {"config.toml": 'api_key = "' + "synthetic-" + 'but-secret-shaped-value"\n'},
    )

    assert result.returncode == 1
    assert "possible assigned secret" in result.stdout


def test_url_embedded_credentials_fail(tmp_path: Path) -> None:
    result = run_safety_check(
        tmp_path,
        {
            "docs/example.md": (
                "https://synthetic-user:" + "synthetic-pass" + "@example.invalid/path\n"
            )
        },
    )

    assert result.returncode == 1
    assert "possible URL-embedded credentials" in result.stdout


def test_private_ipv4_fails_but_public_ipv4_passes(tmp_path: Path) -> None:
    private_result = run_safety_check(
        tmp_path / "private",
        {"docs/example.md": "Synthetic private address " + "10.23." + "45.67\n"},
    )
    public_result = run_safety_check(
        tmp_path / "public",
        {"docs/example.md": "Public resolver example 8.8.8.8\n"},
    )

    assert private_result.returncode == 1
    assert "private/sensitive IP address" in private_result.stdout
    assert public_result.returncode == 0


def test_key_like_file_suffix_fails(tmp_path: Path) -> None:
    result = run_safety_check(
        tmp_path,
        {"fixtures/synthetic.pem": "not-a-real-key\n"},
    )

    assert result.returncode == 1
    assert "prohibited key/certificate-like file" in result.stdout
