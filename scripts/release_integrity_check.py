"""Fail-closed integrity checks for release artifacts."""

from __future__ import annotations

import ast
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

REQUIRED_ROOT_FILES = (
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "CHANGELOG.md",
    "RELEASING.md",
    "pyproject.toml",
    "src/freqtrade_truth/py.typed",
)

PROHIBITED_SUFFIXES = {
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
}


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle).get("project")

    if not isinstance(project, dict):
        raise RuntimeError("pyproject.toml is missing [project]")
    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("pyproject.toml project.version must be a non-empty string")
    return version


def _source_version() -> str:
    path = ROOT / "src" / "freqtrade_truth" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
        raise RuntimeError("__version__ must be assigned a literal string")

    raise RuntimeError("src/freqtrade_truth/__init__.py is missing __version__")


def _unsafe_archive_member(raw_name: str) -> str | None:
    path = PurePosixPath(raw_name)
    if path.is_absolute() or ".." in path.parts:
        return "unsafe archive path"

    name = path.name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "environment file in release artifact"
    if path.suffix.lower() in PROHIBITED_SUFFIXES:
        return "key/certificate-like file in release artifact"
    if "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}:
        return "bytecode/cache file in release artifact"
    return None


def _wheel_findings(wheel: Path, version: str) -> list[str]:
    findings: list[str] = []
    expected_prefix = f"freqtrade_truth-{version}-"
    if not wheel.name.startswith(expected_prefix):
        findings.append(f"{wheel.name}: unexpected wheel filename")

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for name in names:
            unsafe = _unsafe_archive_member(name)
            if unsafe:
                findings.append(f"{wheel.name}:{name}: {unsafe}")

        if "freqtrade_truth/py.typed" not in names:
            findings.append(f"{wheel.name}: missing freqtrade_truth/py.typed")
        if any(name.startswith(("tests/", "scripts/")) for name in names):
            findings.append(f"{wheel.name}: development-only files leaked into wheel")

        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            findings.append(f"{wheel.name}: expected exactly one dist-info/METADATA")
        else:
            metadata = archive.read(metadata_names[0]).decode("utf-8")
            if f"Name: freqtrade-truth\n" not in metadata:
                findings.append(f"{wheel.name}: package name metadata mismatch")
            if f"Version: {version}\n" not in metadata:
                findings.append(f"{wheel.name}: package version metadata mismatch")

    return findings


def _sdist_findings(sdist: Path, version: str) -> list[str]:
    findings: list[str] = []
    expected_name = f"freqtrade_truth-{version}.tar.gz"
    if sdist.name != expected_name:
        findings.append(f"{sdist.name}: unexpected source distribution filename")

    with tarfile.open(sdist, mode="r:gz") as archive:
        members = archive.getmembers()
        root_prefix = f"freqtrade_truth-{version}/"
        for member in members:
            unsafe = _unsafe_archive_member(member.name)
            if unsafe:
                findings.append(f"{sdist.name}:{member.name}: {unsafe}")
            if member.name != root_prefix.rstrip("/") and not member.name.startswith(root_prefix):
                findings.append(f"{sdist.name}:{member.name}: member escapes expected archive root")

    return findings


def collect_findings() -> list[str]:
    findings: list[str] = []

    for relative_path in REQUIRED_ROOT_FILES:
        if not (ROOT / relative_path).is_file():
            findings.append(f"{relative_path}: required release file is missing")

    project_version = _project_version()
    source_version = _source_version()
    if project_version != source_version:
        findings.append(
            f"version mismatch: pyproject={project_version!r}, source={source_version!r}"
        )

    if not DIST.is_dir():
        findings.append("dist/: build artifacts are missing")
        return findings

    wheels = sorted(DIST.glob("*.whl"))
    sdists = sorted(DIST.glob("*.tar.gz"))

    if len(wheels) != 1:
        findings.append(f"dist/: expected exactly one wheel, found {len(wheels)}")
    else:
        findings.extend(_wheel_findings(wheels[0], project_version))

    if len(sdists) != 1:
        findings.append(f"dist/: expected exactly one source distribution, found {len(sdists)}")
    else:
        findings.extend(_sdist_findings(sdists[0], project_version))

    return findings


def main() -> int:
    findings = collect_findings()
    if findings:
        print("RELEASE INTEGRITY CHECK FAILED")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("RELEASE INTEGRITY CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
