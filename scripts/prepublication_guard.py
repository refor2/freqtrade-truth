"""Local fail-closed Git staged-blob and outgoing-commit safety gate.

Run from .githooks before any commit or push. Unlike CI checks, this scans
exact Git objects before they leave the developer's machine. Never prints
secret contents, only relative filenames and generic finding categories.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import PurePosixPath

from public_safety_check import (
    IPV4_PATTERN,
    SECRET_PATTERNS,
    contains_private_project_label,
    is_private_or_sensitive_ip,
)

_SHA = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_MAX_BLOB_BYTES = 2_000_000
_MAX_COMMITS_PER_PUSH = 5_000
_PROHIBITED_NAMES = {
    ".env",
    ".envrc",
    "id_rsa",
    "id_ecdsa",
    "id_ed25519",
    "id_dsa",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
    "secrets.json",
    "secrets.yaml",
}
_PROHIBITED_PATH_COMPONENTS = {"credentials", "secrets", "private"}
_PROHIBITED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".p8"}


class UnsafeSourceError(RuntimeError):
    """A Git object could not be inspected safely before publication."""


def _git(*args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(["git", *args], input=input_bytes, check=False, capture_output=True)
    if result.returncode:
        raise UnsafeSourceError("Git object inspection failed; operation blocked")
    return result.stdout


def _validate_sha(raw: str) -> str:
    if not _SHA.fullmatch(raw):
        raise UnsafeSourceError("Unrecognized Git object identifier; operation blocked")
    return raw


def _staged_entries() -> list[tuple[str, str, str]]:
    entries = []
    for entry in _git("ls-files", "--stage", "-z").split(b"\x00"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, sha, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise UnsafeSourceError("Unmerged Git index; operation blocked")
        entries.append((mode, _validate_sha(sha), raw_path.decode("utf-8")))
    return entries


def _tree_entries(commit: str) -> list[tuple[str, str, str]]:
    entries = []
    for entry in _git("ls-tree", "-rz", commit).split(b"\x00"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, sha = metadata.decode("ascii").split()
        if kind != "blob":
            raise UnsafeSourceError("Uninspectable Git tree entry; operation blocked")
        entries.append((mode, _validate_sha(sha), raw_path.decode("utf-8")))
    return entries


def _inspect_entry(mode: str, blob_sha: str, name: str) -> list[str]:
    path = PurePosixPath(name)
    issues = []
    if (
        path.name in _PROHIBITED_NAMES or path.name.startswith(".env.")
    ) and path.name != ".env.example":
        issues.append("prohibited tracked filename")
    if any(component in _PROHIBITED_PATH_COMPONENTS for component in path.parts[:-1]):
        issues.append("private credentials directory")
    if path.suffix.lower() in _PROHIBITED_SUFFIXES:
        issues.append("prohibited key/certificate-like file")
    if mode not in ("100644", "100755"):
        issues.append("uninspectable Git entry mode")
        return issues

    size = int(_git("cat-file", "-s", blob_sha).decode("ascii").strip())
    if size > _MAX_BLOB_BYTES:
        issues.append("oversized file requires separate private review")
        return issues

    contents = _git("cat-file", "blob", blob_sha)
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        issues.append("unscannable binary content")
        return issues
    if "\x00" in text:
        issues.append("unscannable binary content")
        return issues

    for label, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            issues.append(f"possible {label}")
    if contains_private_project_label(text):
        issues.append("private deployment label in public source")
    if any(is_private_or_sensitive_ip(raw) for raw in IPV4_PATTERN.findall(text)):
        issues.append("private/sensitive IP address")
    return issues


def _outgoing_commits(stdin: str) -> list[str]:
    commits: set[str] = set()
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) != 4:
            raise UnsafeSourceError("Malformed Git pre-push input; operation blocked")
        _local_ref, local_sha, _remote_ref, remote_sha = parts
        if set(local_sha) == {"0"}:  # Ref deletion sends no new objects.
            continue
        local_sha = _validate_sha(local_sha)
        remote_sha = _validate_sha(remote_sha)
        command: tuple[str, ...]
        if set(remote_sha) == {"0"}:
            # New remote branch: scan *all* reachable commits to avoid
            # hiding a secret in any unadvertised parent/history object.
            command = ("rev-list", local_sha)
        else:
            command = ("rev-list", local_sha, f"^{remote_sha}")
        for sha in _git(*command).decode("ascii").splitlines():
            commits.add(_validate_sha(sha))
            if len(commits) > _MAX_COMMITS_PER_PUSH:
                raise UnsafeSourceError("Excessive outgoing history; manual review required")
    return sorted(commits)


def _check(mode: str) -> int:
    findings: list[str] = []
    if mode == "--staged":
        snapshots = [("staged", _staged_entries())]
    else:
        snapshots = [(sha[:12], _tree_entries(sha)) for sha in _outgoing_commits(sys.stdin.read())]
    checked: set[tuple[str, str, str]] = set()
    for source, entries in snapshots:
        for file_mode, blob_sha, path in entries:
            key = (file_mode, blob_sha, path)
            if key in checked:
                continue
            checked.add(key)
            for issue in _inspect_entry(file_mode, blob_sha, path):
                findings.append(f"{source} {path}: {issue}")

    if findings:
        print("PRE-PUBLICATION SAFETY BLOCKED (no secret contents printed)")
        for finding in findings[:30]:
            print(f"- {finding}")
        if len(findings) > 30:
            print(f"- Additional findings: {len(findings) - 30}")
        return 1
    print("PRE-PUBLICATION SAFETY PASSED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Git objects before GitHub publication")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--staged", action="store_true")
    modes.add_argument("--pre-push", action="store_true")
    arguments = parser.parse_args()
    try:
        return _check("--staged" if arguments.staged else "--pre-push")
    except (UnsafeSourceError, ValueError, UnicodeError, OSError) as exc:
        print(f"PRE-PUBLICATION SAFETY BLOCKED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
