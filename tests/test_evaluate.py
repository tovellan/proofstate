from __future__ import annotations

import ast
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from proofstate.document import load_document as load_evidence_document
from proofstate.errors import ErrorCode, ProofStateError
from proofstate.evaluate import _freeze_json_value, evaluate_scorecard
from proofstate.evidence import (
    EvidenceResult,
    _AttestationCache,
    _EvaluationWorkBudget,
    _FileDigestCache,
    verify_attestation,
    verify_machine_evidence,
)
from proofstate.git import GitRepository
from proofstate.models import AttestationEvidence, GateLevel, MachineEvidence
from tests.conftest import RepositoryFixture, git, write_json

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def dependency_chain(size: int, *, cycle: bool = False) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    for index in range(size):
        dependencies = [] if index == 0 else [f"item-{index - 1}"]
        if cycle and index == 0:
            dependencies = [f"item-{size - 1}"]
        assertions.append(
            {
                "id": f"item-{index}",
                "title": f"Item {index}",
                "severity": "low",
                "failure_cap": "none",
                "depends_on": dependencies,
                "evidence": {"machine": [{"type": "file", "path": "missing.txt"}]},
            }
        )
    return assertions


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

    first = result.to_dict()
    assertions = cast(list[dict[str, Any]], first["assertions"])
    cast(list[str], assertions[1]["dependencies"]).append("mutated")
    assert result.assertions[1].dependencies == ["source-present"]
    second_assertions = cast(list[dict[str, Any]], result.to_dict()["assertions"])
    assert second_assertions[1]["dependencies"] == ["source-present"]


@pytest.mark.parametrize("suffix", [" ", "\t", "\n", "\N{NO-BREAK SPACE}"])
def test_repository_discovery_preserves_trailing_path_characters(
    tmp_path: Path,
    suffix: str,
) -> None:
    shadow = tmp_path / "repository"
    selected = tmp_path / f"repository{suffix}"
    shadow.mkdir()
    selected.mkdir()
    git(shadow, "init", "-b", "main")
    git(selected, "init", "-b", "main")

    repository = GitRepository.discover(selected)

    assert repository.root == selected.resolve()


def test_repository_discovery_rejects_relative_git_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.CompletedProcess(
        ["git"],
        0,
        stdout=b"relative-root\n",
        stderr=b"",
    )
    monkeypatch.setattr(
        GitRepository,
        "_execute",
        staticmethod(lambda args, *, check=True: process),
    )

    with pytest.raises(ProofStateError) as raised:
        GitRepository.discover(Path("."))

    assert raised.value.code == ErrorCode.GIT_COMMAND_FAILED


def test_evidence_cache_key_is_type_exact_order_independent_and_surrogate_safe() -> None:
    assert _freeze_json_value(True) != _freeze_json_value(1)
    assert _freeze_json_value(1) != _freeze_json_value(1.0)
    assert _freeze_json_value(-0.0) == _freeze_json_value(0.0)
    assert _freeze_json_value({"a": 1, "b": ["\ud800"]}) == _freeze_json_value(
        {"b": ["\ud800"], "a": 1}
    )


def test_surrogate_artifact_expectation_evaluates_without_serialization_error(
    repository_fixture: RepositoryFixture,
) -> None:
    write_json(repository_fixture.root / "evidence/report.json", {"value": "\ud800"})
    git(repository_fixture.root, "add", "--", "evidence/report.json")
    git(repository_fixture.root, "commit", "-m", "Record surrogate fixture")
    target_commit = git(repository_fixture.root, "rev-parse", "HEAD")
    scorecard = repository_fixture.copy_scorecard()
    scorecard["repository"]["commit"] = target_commit
    scorecard["assertions"] = [scorecard["assertions"][2]]
    scorecard["assertions"][0]["depends_on"] = []
    scorecard["assertions"][0]["evidence"]["machine"] = [
        {
            "type": "artifact",
            "path": "evidence/report.json",
            "format": "json",
            "checks": [{"pointer": "/value", "operator": "equals", "expected": "\ud800"}],
        }
    ]
    repository_fixture.commit_policy(scorecard)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.assertions[0].evidence[0].code == "PSE000_VERIFIED"


def test_oversized_scorecard_path_uses_stable_argument_error_before_tree_lookup(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_execute = GitRepository._execute
    tree_calls = 0

    def counting_execute(
        args: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal tree_calls
        if len(args) > 3 and args[3] == "ls-tree":
            tree_calls += 1
        return original_execute(args, check=check)

    monkeypatch.setattr(GitRepository, "_execute", staticmethod(counting_execute))

    with pytest.raises(ProofStateError) as raised:
        evaluate_scorecard(
            "x" * 16_384,
            repository_path=repository_fixture.root,
            evaluated_at=NOW,
        )

    assert raised.value.code == ErrorCode.INVALID_ARGUMENT
    assert tree_calls == 0


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


def test_git_replacement_ref_cannot_change_pinned_evidence(
    repository_fixture: RepositoryFixture,
) -> None:
    original_tree = git(
        repository_fixture.root,
        "rev-parse",
        f"{repository_fixture.target_commit}^{{tree}}",
    )
    (repository_fixture.root / "src/widget.py").write_text("VALUE = 999\n", encoding="utf-8")
    git(repository_fixture.root, "add", "--", "src/widget.py")
    git(repository_fixture.root, "commit", "-m", "Create replacement content")
    replacement_commit = git(repository_fixture.root, "rev-parse", "HEAD")
    replacement_tree = git(repository_fixture.root, "rev-parse", f"{replacement_commit}^{{tree}}")
    git(
        repository_fixture.root,
        "replace",
        repository_fixture.target_commit,
        replacement_commit,
    )

    assert (
        git(
            repository_fixture.root,
            "rev-parse",
            f"{repository_fixture.target_commit}^{{tree}}",
        )
        == replacement_tree
    )
    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        scorecard_ref=repository_fixture.policy_commit,
        evaluated_at=NOW,
    )

    assert result.passed
    assert result.evidence_tree == original_tree


def test_pathspec_metacharacters_are_literal_evidence_paths(
    repository_fixture: RepositoryFixture,
) -> None:
    evidence_paths = [
        "evidence/[report].json",
        "evidence/*literal*.json",
        "evidence/:(glob)report.json",
        "evidence/tab\tname.json",
        "evidence/line\nname.json",
    ]
    for evidence_path in evidence_paths:
        (repository_fixture.root / evidence_path).write_text("{}\n", encoding="utf-8")
        git(repository_fixture.root, "add", f":(literal){evidence_path}")
    git(repository_fixture.root, "commit", "-m", "Add literal pathspec evidence")
    target_commit = git(repository_fixture.root, "rev-parse", "HEAD")
    scorecard = repository_fixture.copy_scorecard()
    scorecard["repository"]["commit"] = target_commit
    scorecard["assertions"] = [scorecard["assertions"][0]]
    scorecard["assertions"][0]["evidence"]["machine"] = [
        {"type": "file", "path": evidence_path} for evidence_path in evidence_paths
    ]
    repository_fixture.commit_policy(scorecard)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.passed
    assert [evidence.code for evidence in result.assertions[0].evidence] == [
        "PSE000_VERIFIED"
    ] * len(evidence_paths)


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


def test_maximum_dependency_chain_evaluates_without_recursion(
    repository_fixture: RepositoryFixture,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = dependency_chain(1_000)
    repository_fixture.commit_policy(scorecard)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.assertions[0].status == "fail"
    assert [assertion.status for assertion in result.assertions[1:]] == ["blocked"] * 999


def test_maximum_dependency_cycle_is_reported_as_invalid_scorecard(
    repository_fixture: RepositoryFixture,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = dependency_chain(1_000, cycle=True)
    repository_fixture.commit_policy(scorecard)

    with pytest.raises(ProofStateError) as caught:
        evaluate_scorecard(
            ".proofstate/scorecard.yaml",
            repository_path=repository_fixture.root,
            evaluated_at=NOW,
        )

    assert caught.value.code == ErrorCode.INVALID_SCORECARD


def test_repeated_machine_evidence_is_verified_once_per_evaluation(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    repeated = {"type": "file", "path": "src/widget.py"}
    scorecard["assertions"] = [
        {
            "id": f"repeated-{index}",
            "title": f"Repeated evidence {index}",
            "severity": "low",
            "evidence": {"machine": [repeated.copy() for _ in range(100)]},
        }
        for index in range(10)
    ]
    repository_fixture.commit_policy(scorecard)
    original = verify_machine_evidence
    calls = 0

    def counting_verify(
        evidence: MachineEvidence,
        repository: GitRepository,
        commit: str,
        max_bytes: int,
        *,
        file_digest_cache: _FileDigestCache | None = None,
        work_budget: _EvaluationWorkBudget | None = None,
    ) -> EvidenceResult:
        nonlocal calls
        calls += 1
        return original(
            evidence,
            repository,
            commit,
            max_bytes,
            file_digest_cache=file_digest_cache,
            work_budget=work_budget,
        )

    monkeypatch.setattr("proofstate.evaluate.verify_machine_evidence", counting_verify)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.passed is True
    assert calls == 1
    assert sum(len(assertion.evidence) for assertion in result.assertions) == 1_000
    assert result.assertions[0].evidence[0] is not result.assertions[0].evidence[1]


def test_distinct_file_digest_expectations_share_one_blob_read(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = [
        {
            "id": "shared-file",
            "title": "Digest expectations share one immutable blob read",
            "severity": "low",
            "evidence": {
                "machine": [
                    {"type": "file", "path": "src/widget.py", "sha256": "0" * 64},
                    {"type": "file", "path": "src/widget.py", "sha256": "1" * 64},
                    {"type": "file", "path": "src/widget.py"},
                ]
            },
        }
    ]
    repository_fixture.commit_policy(scorecard)
    original_read_blob = GitRepository.read_blob
    reads = 0

    def counting_read_blob(
        repository: GitRepository,
        commit: str,
        path: str,
        *,
        max_bytes: int,
    ) -> bytes:
        nonlocal reads
        if path == "src/widget.py":
            reads += 1
        return original_read_blob(repository, commit, path, max_bytes=max_bytes)

    monkeypatch.setattr(GitRepository, "read_blob", counting_read_blob)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert reads == 1
    assert [evidence.code for evidence in result.assertions[0].evidence] == [
        "PSE103_DIGEST_MISMATCH",
        "PSE103_DIGEST_MISMATCH",
        "PSE000_VERIFIED",
    ]


def test_distinct_missing_paths_use_bounded_batched_tree_lookups(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [f"missing/evidence-{index:04d}.txt" for index in range(513)]
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = [
        {
            "id": f"missing-{group}",
            "title": f"Missing evidence group {group}",
            "severity": "low",
            "evidence": {
                "machine": [
                    {"type": "file", "path": path}
                    for path in paths[group * 100 : (group + 1) * 100]
                ]
            },
        }
        for group in range(6)
        if paths[group * 100 : (group + 1) * 100]
    ]
    repository_fixture.commit_policy(scorecard)
    original_execute = GitRepository._execute
    tree_calls: list[list[str]] = []

    def counting_execute(
        args: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        if len(args) > 5 and args[3] == "ls-tree":
            tree_calls.append(args)
        return original_execute(args, check=check)

    monkeypatch.setattr(GitRepository, "_execute", staticmethod(counting_execute))

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    target_calls = [call for call in tree_calls if call[6] == repository_fixture.target_commit]
    requested = [path for call in target_calls for path in call[8:]]
    assert [len(call[8:]) for call in target_calls] == [256, 256, 1]
    assert all(sum(len(path.encode()) + 1 for path in call[8:]) <= 16_384 for call in target_calls)
    assert requested == paths
    assert [
        evidence.code for assertion in result.assertions for evidence in assertion.evidence
    ] == ["PSE101_FILE_MISSING"] * len(paths)


def test_evaluation_fails_closed_after_distinct_source_budget(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [f"evidence/alias-{index}.txt" for index in range(3)]
    for path in paths:
        destination = repository_fixture.root / path
        destination.write_text("same immutable content\n", encoding="utf-8")
        git(repository_fixture.root, "add", "--", path)
    git(repository_fixture.root, "commit", "-m", "Add aliased synthetic evidence")
    target = git(repository_fixture.root, "rev-parse", "HEAD")
    scorecard = repository_fixture.copy_scorecard()
    scorecard["repository"]["commit"] = target
    scorecard["assertions"] = [
        {
            "id": "source-budget",
            "title": "Source work remains bounded",
            "severity": "high",
            "evidence": {"machine": [{"type": "file", "path": path} for path in paths]},
        }
    ]
    repository_fixture.commit_policy(scorecard)
    monkeypatch.setattr("proofstate.evidence.EVALUATION_MAX_EVIDENCE_SOURCES", 2)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert [evidence.code for evidence in result.assertions[0].evidence] == [
        "PSE000_VERIFIED",
        "PSE000_VERIFIED",
        "PSE104_EVALUATION_LIMIT",
    ]


def test_directory_path_does_not_poison_batched_descendant_or_unrelated_entry(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = [
        {
            "id": "prefix-paths",
            "title": "Tree entry batching isolates prefix paths",
            "severity": "high",
            "evidence": {
                "machine": [
                    {"type": "file", "path": "src"},
                    {"type": "file", "path": "src/widget.py"},
                    {"type": "file", "path": "evidence/report.json"},
                ]
            },
        }
    ]
    repository_fixture.commit_policy(scorecard)
    original_execute = GitRepository._execute
    target_chunks: list[list[str]] = []

    def counting_execute(
        args: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        if len(args) > 6 and args[3] == "ls-tree" and args[6] == repository_fixture.target_commit:
            target_chunks.append(args[8:])
        return original_execute(args, check=check)

    monkeypatch.setattr(GitRepository, "_execute", staticmethod(counting_execute))

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert [evidence.code for evidence in result.assertions[0].evidence] == [
        "PSE101_FILE_MISSING",
        "PSE000_VERIFIED",
        "PSE000_VERIFIED",
    ]
    assert target_chunks == [["src"], ["src/widget.py", "evidence/report.json"]]


def test_batched_tree_failure_is_cached_and_fails_affected_evidence_closed(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = [
        {
            "id": "tree-failure",
            "title": "Tree lookup failures fail closed",
            "severity": "high",
            "evidence": {
                "machine": [
                    {"type": "file", "path": path}
                    for path in ("src/widget.py", "tests/test_widget.py", "evidence/report.json")
                ]
            },
        }
    ]
    repository_fixture.commit_policy(scorecard)
    original_execute = GitRepository._execute
    target_calls = 0

    def failing_execute(
        args: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal target_calls
        if len(args) > 6 and args[3] == "ls-tree" and args[6] == repository_fixture.target_commit:
            target_calls += 1
            raise ProofStateError(ErrorCode.GIT_COMMAND_FAILED, "synthetic batch failure")
        return original_execute(args, check=check)

    monkeypatch.setattr(GitRepository, "_execute", staticmethod(failing_execute))

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert [evidence.code for evidence in result.assertions[0].evidence] == [
        "PSE900_INTERNAL_ERROR"
    ] * 3
    assert target_calls == 1


def test_tree_lookup_chunk_limit_returns_stable_evaluation_limit(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = [
        {
            "id": "tree-limit",
            "title": "Tree lookup work remains bounded",
            "severity": "high",
            "evidence": {
                "machine": [
                    {"type": "file", "path": "src"},
                    {"type": "file", "path": "src/widget.py"},
                    {"type": "file", "path": "evidence/report.json"},
                ]
            },
        }
    ]
    repository_fixture.commit_policy(scorecard)
    monkeypatch.setattr("proofstate.git.ENTRY_PREFETCH_MAX_CHUNKS", 2)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert [evidence.code for evidence in result.assertions[0].evidence] == [
        "PSE101_FILE_MISSING",
        "PSE104_EVALUATION_LIMIT",
        "PSE104_EVALUATION_LIMIT",
    ]


def test_oversized_evidence_path_is_rejected_before_git_argv_construction(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = f"evidence/{'x' * 16_384}"
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = [
        {
            "id": "path-budget",
            "title": "Git argument construction remains bounded",
            "severity": "high",
            "evidence": {"machine": [{"type": "file", "path": evidence_path}]},
        }
    ]
    repository_fixture.commit_policy(scorecard)
    original_execute = GitRepository._execute
    target_tree_calls = 0

    def counting_execute(
        args: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal target_tree_calls
        if len(args) > 6 and args[3] == "ls-tree" and args[6] == repository_fixture.target_commit:
            target_tree_calls += 1
        return original_execute(args, check=check)

    monkeypatch.setattr(GitRepository, "_execute", staticmethod(counting_execute))

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.assertions[0].evidence[0].code == "PSE104_EVALUATION_LIMIT"
    assert target_tree_calls == 0


def test_distinct_artifact_checks_share_one_blob_read_and_parse(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = [
        {
            "id": "shared-artifact",
            "title": "Artifact checks share one immutable parsed document",
            "severity": "low",
            "evidence": {
                "machine": [
                    {
                        "type": "artifact",
                        "path": "evidence/report.json",
                        "format": "json",
                        "checks": [
                            {
                                "pointer": "/status",
                                "operator": "equals",
                                "expected": "ready",
                            }
                        ],
                    },
                    {
                        "type": "artifact",
                        "path": "evidence/report.json",
                        "format": "json",
                        "checks": [
                            {
                                "pointer": "/tests/passed",
                                "operator": "gte",
                                "expected": 1,
                            }
                        ],
                    },
                ]
            },
        }
    ]
    repository_fixture.commit_policy(scorecard)
    original_read_blob = GitRepository.read_blob
    reads = 0
    parses = 0

    def counting_read_blob(
        repository: GitRepository,
        commit: str,
        path: str,
        *,
        max_bytes: int,
    ) -> bytes:
        nonlocal reads
        if path == "evidence/report.json":
            reads += 1
        return original_read_blob(repository, commit, path, max_bytes=max_bytes)

    def counting_parse(content: bytes, *, format_hint: str | None = None) -> Any:
        nonlocal parses
        parses += 1
        return load_evidence_document(content, format_hint=format_hint)

    monkeypatch.setattr(GitRepository, "read_blob", counting_read_blob)
    monkeypatch.setattr("proofstate.evidence.load_document", counting_parse)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.passed is True
    assert reads == 1
    assert parses == 1
    assert [evidence.code for evidence in result.assertions[0].evidence] == [
        "PSE000_VERIFIED",
        "PSE000_VERIFIED",
    ]


def test_distinct_test_symbols_share_source_read_and_parse_during_evaluation(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = [
        {
            "id": "shared-test-source",
            "title": "Distinct symbols share one source parse",
            "severity": "low",
            "evidence": {
                "machine": [
                    {
                        "type": "test_symbol",
                        "path": "tests/test_widget.py",
                        "symbol": symbol,
                        "framework": "pytest",
                    }
                    for symbol in ("test_widget", "TestWidget.test_async")
                ]
            },
        }
    ]
    repository_fixture.commit_policy(scorecard)
    original_read_blob = GitRepository.read_blob
    original_parse = ast.parse
    reads = 0
    parses = 0

    def counting_read_blob(
        repository: GitRepository,
        commit: str,
        path: str,
        *,
        max_bytes: int,
    ) -> bytes:
        nonlocal reads
        if path == "tests/test_widget.py":
            reads += 1
        return original_read_blob(repository, commit, path, max_bytes=max_bytes)

    def counting_parse(*args: Any, **kwargs: Any) -> ast.AST:
        nonlocal parses
        parses += 1
        return cast(ast.AST, original_parse(*args, **kwargs))

    monkeypatch.setattr(GitRepository, "read_blob", counting_read_blob)
    monkeypatch.setattr("proofstate.evidence.ast.parse", counting_parse)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.passed is True
    assert reads == 1
    assert parses == 1
    assert [evidence.details for evidence in result.assertions[0].evidence] == [
        {"symbol": "test_widget"},
        {"symbol": "TestWidget.test_async"},
    ]


def test_test_symbol_git_failure_fails_closed_during_evaluation(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_blob = GitRepository.read_blob

    def failing_test_read(
        repository: GitRepository,
        commit: str,
        path: str,
        *,
        max_bytes: int,
    ) -> bytes:
        if path == "tests/test_widget.py":
            raise ProofStateError(ErrorCode.GIT_COMMAND_FAILED, "synthetic Git failure")
        return original_read_blob(repository, commit, path, max_bytes=max_bytes)

    monkeypatch.setattr(GitRepository, "read_blob", failing_test_read)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    test_result = result.assertions[1].evidence[0]
    assert result.passed is False
    assert test_result.code == "PSE900_INTERNAL_ERROR"
    assert test_result.message == "Git object verification failed closed"


def test_repeated_attestation_is_verified_once_per_assertion(
    repository_fixture: RepositoryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assertion_ids = ("attestation-a", "attestation-b")
    repository_fixture.attestation["scope"]["assertions"] = list(assertion_ids)
    repeated = {
        "type": "human_attestation",
        "path": ".proofstate/attestations/review.json",
    }
    scorecard = repository_fixture.copy_scorecard()
    scorecard["assertions"] = [
        {
            "id": assertion_id,
            "title": f"Repeated attestation {assertion_id}",
            "severity": "high",
            "evidence": {"attestations": [repeated.copy() for _ in range(3)]},
        }
        for assertion_id in assertion_ids
    ]
    repository_fixture.commit_policy(scorecard)
    original = verify_attestation
    original_read_blob = GitRepository.read_blob
    calls = 0
    reads = 0

    def counting_verify(
        evidence: AttestationEvidence,
        repository: GitRepository,
        policy_commit: str,
        target_commit: str,
        repository_identity: str,
        assertion_id: str,
        evaluated_at: datetime,
        max_bytes: int,
        *,
        cache: _AttestationCache | None = None,
        work_budget: _EvaluationWorkBudget | None = None,
    ) -> EvidenceResult:
        nonlocal calls
        calls += 1
        return original(
            evidence,
            repository,
            policy_commit,
            target_commit,
            repository_identity,
            assertion_id,
            evaluated_at,
            max_bytes,
            cache=cache,
            work_budget=work_budget,
        )

    def counting_read_blob(
        repository: GitRepository,
        commit: str,
        path: str,
        *,
        max_bytes: int,
    ) -> bytes:
        nonlocal reads
        if path == ".proofstate/attestations/review.json":
            reads += 1
        return original_read_blob(repository, commit, path, max_bytes=max_bytes)

    monkeypatch.setattr("proofstate.evaluate.verify_attestation", counting_verify)
    monkeypatch.setattr(GitRepository, "read_blob", counting_read_blob)

    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=NOW,
    )

    assert result.passed is True
    assert calls == len(assertion_ids)
    assert reads == 1
    assert [len(assertion.evidence) for assertion in result.assertions] == [3, 3]
    assert result.assertions[0].evidence[0] is not result.assertions[0].evidence[1]


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
