from __future__ import annotations

from datetime import UTC, datetime

import pytest

from proofstate.errors import ErrorCode, ProofStateError
from proofstate.evaluate import evaluate_scorecard
from proofstate.models import GateLevel
from tests.conftest import RepositoryFixture, git, write_json

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def test_complete_scorecard_passes(repository_fixture: RepositoryFixture) -> None:
    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.passed
    assert result.achieved_gate == GateLevel.RELEASE
    assert result.evidence_commit == repository_fixture.target_commit
    assert result.scorecard_commit == repository_fixture.policy_commit
    assert [assertion.status for assertion in result.assertions] == ["pass"] * 4
    assert len(result.evidence_tree) == 40
    assert result.to_dict()["schema_version"] == "proofstate.dev/result/v1alpha1"


def test_worktree_edit_cannot_change_tracked_scorecard(
    repository_fixture: RepositoryFixture,
) -> None:
    write_json(repository_fixture.root / ".proofstate/scorecard.yaml", {"invalid": True})

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.passed


def test_missing_machine_evidence_blocks_dependents(
    repository_fixture: RepositoryFixture,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"][0]["evidence"]["machine"][0]["path"] = "src/absent.py"
    repository_fixture.commit_policy(scorecard)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert not result.passed
    assert result.achieved_gate == GateLevel.NONE
    assert [assertion.status for assertion in result.assertions] == [
        "fail",
        "blocked",
        "blocked",
        "blocked",
    ]
    assert result.assertions[0].evidence[0].code == "PSE101_FILE_MISSING"


def test_failure_cap_allows_merge_but_not_release(
    repository_fixture: RepositoryFixture,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"][2]["evidence"]["machine"][0]["checks"][0]["expected"] = "no"
    repository_fixture.commit_policy(scorecard)

    merge_result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        required_gate=GateLevel.MERGE,
        evaluated_at=NOW,
    )
    release_result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        required_gate=GateLevel.RELEASE,
        evaluated_at=NOW,
    )

    assert merge_result.passed
    assert merge_result.achieved_gate == GateLevel.MERGE
    assert not release_result.passed


def test_expired_attestation_fails_closed(repository_fixture: RepositoryFixture) -> None:
    repository_fixture.attestation["expires_at"] = "2026-01-01T00:00:00Z"
    repository_fixture.commit_policy()

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    evidence = result.assertions[-1].evidence[0]
    assert not result.passed
    assert evidence.code == "PSE403_ATTESTATION_EXPIRED"


def test_attestation_must_scope_assertion(repository_fixture: RepositoryFixture) -> None:
    repository_fixture.attestation["scope"]["assertions"] = ["source-present"]
    repository_fixture.commit_policy()

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.assertions[-1].evidence[0].code == "PSE404_ATTESTATION_SCOPE_MISMATCH"


def test_resolvable_but_unrelated_commit_is_rejected(
    repository_fixture: RepositoryFixture,
) -> None:
    tree = git(repository_fixture.root, "rev-parse", f"{repository_fixture.target_commit}^{{tree}}")
    unrelated = git(repository_fixture.root, "commit-tree", tree, "-m", "Unrelated synthetic root")
    scorecard = repository_fixture.copy_scorecard()
    scorecard["repository"]["commit"] = unrelated
    repository_fixture.commit_policy(scorecard)

    with pytest.raises(ProofStateError) as caught:
        evaluate_scorecard(
            ".proofstate/scorecard.yaml",
            repository_path=repository_fixture.root,
            evaluated_at=NOW,
        )

    assert caught.value.code == ErrorCode.UNRELATED_COMMIT


def test_scorecard_cycle_is_rejected(repository_fixture: RepositoryFixture) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"][0]["depends_on"] = ["human-review"]
    repository_fixture.commit_policy(scorecard)

    with pytest.raises(ProofStateError) as caught:
        evaluate_scorecard(
            ".proofstate/scorecard.yaml",
            repository_path=repository_fixture.root,
            evaluated_at=NOW,
        )

    assert caught.value.code == ErrorCode.INVALID_SCORECARD
    assert caught.value.details is not None
    assert "input_value" not in str(caught.value.details).lower()


def test_symlink_is_not_regular_file_evidence(repository_fixture: RepositoryFixture) -> None:
    link = repository_fixture.root / "src/link.py"
    link.symlink_to("widget.py")
    git(repository_fixture.root, "add", "--", "src/link.py")
    git(repository_fixture.root, "commit", "-m", "Add synthetic symlink")
    target = git(repository_fixture.root, "rev-parse", "HEAD")
    scorecard = repository_fixture.copy_scorecard()
    scorecard["repository"]["commit"] = target
    scorecard["assertions"] = [scorecard["assertions"][0]]
    scorecard["assertions"][0]["evidence"]["machine"][0] = {
        "type": "file",
        "path": "src/link.py",
    }
    repository_fixture.commit_policy(scorecard)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.assertions[0].evidence[0].code == "PSE101_FILE_MISSING"
