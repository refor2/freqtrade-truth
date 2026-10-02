from scripts.commit_metadata_check import (
    CommitIdentity,
    find_violations,
    is_allowed_noreply_email,
)


def test_github_noreply_addresses_are_allowed() -> None:
    assert is_allowed_noreply_email("noreply@github.com")
    assert is_allowed_noreply_email("12345+example@users.noreply.github.com")


def test_regular_email_is_not_allowed_for_protected_identity() -> None:
    commits = [
        CommitIdentity(
            sha="synthetic-sha",
            author_name="refor2",
            author_email="private@example.invalid",
            committer_name="GitHub",
            committer_email="noreply@github.com",
        )
    ]

    assert find_violations(commits) == [
        "synthetic-sha: protected author identity uses a non-noreply email"
    ]


def test_unprotected_contributor_is_not_blocked() -> None:
    commits = [
        CommitIdentity(
            sha="synthetic-sha",
            author_name="external-contributor",
            author_email="public@example.invalid",
            committer_name="GitHub",
            committer_email="noreply@github.com",
        )
    ]

    assert find_violations(commits) == []
