"""Dependency-aware, fail-closed scorecard evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from proofstate.document import DocumentError, load_document
from proofstate.errors import ErrorCode, ProofStateError
from proofstate.evidence import EvidenceResult, verify_attestation, verify_machine_evidence
from proofstate.git import GitRepository
from proofstate.models import Assertion, GateLevel, Scorecard, validate_repository_path

SCORECARD_MAX_BYTES = 1_048_576
_GATE_RANK = {
    GateLevel.NONE: 0,
    GateLevel.ADVISORY: 1,
    GateLevel.MERGE: 2,
    GateLevel.RELEASE: 3,
}


@dataclass(frozen=True, slots=True)
class AssertionResult:
    assertion_id: str
    title: str
    severity: str
    failure_cap: str
    status: str
    dependencies: list[str]
    evidence: list[EvidenceResult]

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.assertion_id,
            "title": self.title,
            "severity": self.severity,
            "failure_cap": self.failure_cap,
            "status": self.status,
            "dependencies": self.dependencies,
            "evidence": [result.to_dict() for result in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class Evaluation:
    passed: bool
    required_gate: GateLevel
    achieved_gate: GateLevel
    evaluated_at: datetime
    repository_identity: str
    scorecard_commit: str
    scorecard_tree: str
    evidence_commit: str
    evidence_tree: str
    assertions: list[AssertionResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "proofstate.dev/result/v1alpha1",
            "passed": self.passed,
            "required_gate": self.required_gate.value,
            "achieved_gate": self.achieved_gate.value,
            "evaluated_at": self.evaluated_at.isoformat(),
            "repository": {
                "identity": self.repository_identity,
                "scorecard_commit": self.scorecard_commit,
                "scorecard_tree": self.scorecard_tree,
                "evidence_commit": self.evidence_commit,
                "evidence_tree": self.evidence_tree,
            },
            "assertions": [assertion.to_dict() for assertion in self.assertions],
        }


def _validation_details(error: ValidationError) -> dict[str, Any]:
    return {
        "violations": [
            {
                "location": ".".join(str(part) for part in violation["loc"]),
                "message": violation["msg"],
                "type": violation["type"],
            }
            for violation in error.errors(include_url=False, include_input=False)
        ]
    }


def load_scorecard(
    repository: GitRepository,
    scorecard_path: str,
    scorecard_ref: str,
) -> tuple[Scorecard, str]:
    try:
        normalized_path = validate_repository_path(scorecard_path)
    except ValueError as error:
        raise ProofStateError(ErrorCode.INVALID_ARGUMENT, str(error)) from error
    policy_commit = repository.resolve_commit(scorecard_ref)
    try:
        content = repository.read_blob(
            policy_commit,
            normalized_path,
            max_bytes=SCORECARD_MAX_BYTES,
        )
    except FileNotFoundError as error:
        raise ProofStateError(
            ErrorCode.SCORECARD_NOT_FOUND,
            "scorecard is absent or is not a regular file at the scorecard revision",
        ) from error
    except OverflowError as error:
        raise ProofStateError(
            ErrorCode.SCORECARD_TOO_LARGE,
            "scorecard exceeds the one MiB input limit",
        ) from error
    try:
        document = load_document(content)
    except DocumentError as error:
        raise ProofStateError(ErrorCode.INVALID_DOCUMENT, str(error)) from error
    try:
        return Scorecard.model_validate(document), policy_commit
    except ValidationError as error:
        raise ProofStateError(
            ErrorCode.INVALID_SCORECARD,
            "scorecard does not conform to the versioned schema",
            _validation_details(error),
        ) from error


def _evaluate_assertion(
    assertion: Assertion,
    completed: dict[str, AssertionResult],
    repository: GitRepository,
    scorecard: Scorecard,
    policy_commit: str,
    evaluated_at: datetime,
) -> AssertionResult:
    if any(not completed[dependency].passed for dependency in assertion.depends_on):
        return AssertionResult(
            assertion.id,
            assertion.title,
            assertion.severity.value,
            assertion.failure_cap.value,
            "blocked",
            list(assertion.depends_on),
            [],
        )
    evidence_results = [
        verify_machine_evidence(
            evidence,
            repository,
            scorecard.repository.commit,
            scorecard.settings.max_evidence_bytes,
        )
        for evidence in assertion.evidence.machine
    ]
    evidence_results.extend(
        verify_attestation(
            evidence,
            repository,
            policy_commit,
            scorecard.repository.commit,
            scorecard.repository.identity,
            assertion.id,
            evaluated_at,
            scorecard.settings.max_evidence_bytes,
        )
        for evidence in assertion.evidence.attestations
    )
    status = "pass" if all(result.passed for result in evidence_results) else "fail"
    return AssertionResult(
        assertion.id,
        assertion.title,
        assertion.severity.value,
        assertion.failure_cap.value,
        status,
        list(assertion.depends_on),
        evidence_results,
    )


def evaluate_scorecard(
    scorecard_path: str,
    *,
    repository_path: Path | str | None = None,
    scorecard_ref: str = "HEAD",
    required_gate: GateLevel = GateLevel.RELEASE,
    evaluated_at: datetime | None = None,
) -> Evaluation:
    input_path = Path.cwd() if repository_path is None else Path(repository_path)
    repository = GitRepository.discover(input_path)
    scorecard, policy_commit = load_scorecard(repository, scorecard_path, scorecard_ref)
    object_format = repository.object_format()
    expected_length = 40 if object_format == "sha1" else 64
    if len(scorecard.repository.commit) != expected_length:
        raise ProofStateError(
            ErrorCode.UNRESOLVABLE_COMMIT,
            "evidence commit does not match the repository object format",
        )
    resolved_target = repository.resolve_commit(scorecard.repository.commit)
    if not repository.is_ancestor(resolved_target, policy_commit):
        raise ProofStateError(
            ErrorCode.UNRELATED_COMMIT,
            "evidence commit is not an ancestor of the scorecard revision",
        )
    instant = evaluated_at or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ProofStateError(ErrorCode.INVALID_TIME, "evaluation time must include an offset")

    by_id = {assertion.id: assertion for assertion in scorecard.assertions}
    completed: dict[str, AssertionResult] = {}

    def evaluate(assertion_id: str) -> AssertionResult:
        if assertion_id in completed:
            return completed[assertion_id]
        assertion = by_id[assertion_id]
        for dependency in assertion.depends_on:
            evaluate(dependency)
        result = _evaluate_assertion(
            assertion,
            completed,
            repository,
            scorecard,
            policy_commit,
            instant,
        )
        completed[assertion_id] = result
        return result

    ordered_results = [evaluate(assertion.id) for assertion in scorecard.assertions]
    achieved = GateLevel.RELEASE
    for assertion, result in zip(scorecard.assertions, ordered_results, strict=True):
        if not result.passed:
            cap = GateLevel(assertion.failure_cap.value)
            if _GATE_RANK[cap] < _GATE_RANK[achieved]:
                achieved = cap
    return Evaluation(
        passed=_GATE_RANK[achieved] >= _GATE_RANK[required_gate],
        required_gate=required_gate,
        achieved_gate=achieved,
        evaluated_at=instant,
        repository_identity=scorecard.repository.identity,
        scorecard_commit=policy_commit,
        scorecard_tree=repository.tree_id(policy_commit),
        evidence_commit=resolved_target,
        evidence_tree=repository.tree_id(resolved_target),
        assertions=ordered_results,
    )
