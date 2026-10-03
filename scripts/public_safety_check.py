"""Fail-closed checks for obvious secrets and private infrastructure in tracked files."""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("OpenAI-style secret", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "URL-embedded credentials",
        re.compile(
            r"""(?ix)
            \bhttps?://
            [^\s/:@]+:
            (?!placeholder@|redacted@|changeme@|dummy@|test@)
            [^\s/@]+@
            [^\s/]+
            """
        ),
    ),
    (
        "assigned secret",
        re.compile(
            r"""(?ix)
            \b(?:api[_-]?key|secret|client[_-]?secret|password|passphrase|access[_-]?token|auth[_-]?token)
            \s*[:=]\s*
            ["'](?!\s*(?:example|placeholder|redacted|changeme|dummy|test|<|\$\{))
            [^"'\r\n]{8,}
            ["']
            """
        ),
    ),
)

IPV4_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

PROHIBITED_TRACKED_NAMES = {
    ".env",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}

PROHIBITED_SUFFIXES = {
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
}

TEXT_SUFFIX_ALLOWLIST = {
    "",
    ".cfg",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

EXCLUDED_CONTENT_PATHS = {
    "scripts/public_safety_check.py",
}

ALLOWED_IPS = {
    ipaddress.ip_address("127.0.0.1"),
}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
    )
    return [ROOT / item.decode("utf-8") for item in output.split(b"\0") if item]


def has_prohibited_tracked_name(path: Path) -> bool:
    if path.name in PROHIBITED_TRACKED_NAMES:
        return True
    return path.name.startswith(".env.") and path.name != ".env.example"


def should_scan_content(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if rel in EXCLUDED_CONTENT_PATHS:
        return False
    if path.name == ".env.example":
        return True
    return path.suffix.lower() in TEXT_SUFFIX_ALLOWLIST


def is_private_or_sensitive_ip(raw: str) -> bool:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False

    if address in ALLOWED_IPS:
        return False

    return bool(
        address.is_private or address.is_link_local or address.is_loopback or address.is_reserved
    )


def main() -> int:
    findings: list[str] = []

    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()

        if has_prohibited_tracked_name(path):
            findings.append(f"{rel}: prohibited tracked filename")

        if path.suffix.lower() in PROHIBITED_SUFFIXES:
            findings.append(f"{rel}: prohibited key/certificate-like file")

        if not path.is_file() or not should_scan_content(path):
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"{rel}: possible {label}")

        for raw_ip in IPV4_PATTERN.findall(text):
            if is_private_or_sensitive_ip(raw_ip):
                findings.append(f"{rel}: private/sensitive IP address {raw_ip}")

    if findings:
        print("PUBLIC SAFETY CHECK FAILED")
        for finding in findings:
            print(f"- {finding}")
        print("Remove or replace the flagged material before publication.")
        return 1

    print("PUBLIC SAFETY CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
