"""Fail closed when protected project identities expose a non-noreply email."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROTECTED_IDENTITIES = frozenset({"refor2"})
GITHUB_NOREPLY = "noreply@github.com"
GITHUB_USER_NOREPLY_SUFFIX = "@users.noreply.github.com"


@dataclass(frozen=True, slots=True)
class CommitIdentity:
    sha: str
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str


def is_allowed_noreply_email(email: str) -> bool:
    normalized = email.strip().lower()
    return normalized == GITHUB_NOREPLY or normalized.endswith(GITHUB_USER_NOREPLY_SUFFIX)


def reachable_commit_identities() -> list[CommitIdentity]:
    output = subprocess.check_output(
        ["git", "log", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce"],
        cwd=ROOT,
    )

    commits: list[CommitIdentity] = []
    for raw_line in output.splitlines():
        if not raw_line:
            continue
        fields = raw_line.decode("utf-8").split("\0")
        if len(fields) != 5:
            raise RuntimeError("unexpected git log metadata format")
        commits.append(CommitIdentity(*fields))

    return commits


def find_violations(commits: list[CommitIdentity]) -> list[str]:
    findings: list[str] = []

    for commit in commits:
        if (
            commit.author_name in PROTECTED_IDENTITIES
            and not is_allowed_noreply_email(commit.author_email)
        ):
            findings.append(f"{commit.sha}: protected author identity uses a non-noreply email")

        if (
            commit.committer_name in PROTECTED_IDENTITIES
            and not is_allowed_noreply_email(commit.committer_email)
        ):
            findings.append(f"{commit.sha}: protected committer identity uses a non-noreply email")

    return findings


def main() -> int:
    findings = find_violations(reachable_commit_identities())

    if findings:
        print("COMMIT METADATA PRIVACY CHECK FAILED")
        for finding in findings:
            print(f"- {finding}")
        print("Use a GitHub noreply address before publishing commits.")
        return 1

    print("COMMIT METADATA PRIVACY CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
