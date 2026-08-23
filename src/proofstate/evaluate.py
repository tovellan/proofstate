"""Dependency-aware, fail-closed scorecard evaluation."""

from __future__ import annotations

from collections import deque
from collections.abc import Hashable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from proofstate.document import DocumentError, load_document
from proofstate.errors import ErrorCode, ProofStateError
from proofstate.evidence import (
    EvidenceResult,
    _ArtifactCache,
    _AttestationCache,
    _EvaluationWorkBudget,
    _FileDigestCache,
    _TestSymbolCache,
    verify_attestation,
    verify_machine_evidence,
)
from proofstate.git import ENTRY_PREFETCH_MAX_PATH_BYTES, GitRepository
from proofstate.models import (
    Assertion,
    FileEvidence,
    GateLevel,
    Scorecard,
    TestSymbolEvidence,
    validate_repository_path,
)

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
            "dependencies": list(self.dependencies),
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


def _freeze_json_value(value: Any) -> Hashable:
    """Return an immutable, type-exact cache key for a JSON value."""
    value_type = type(value)
    if value is None:
        return ("null",)
    if value_type is bool:
        return ("boolean", value)
    if value_type is int:
        return ("integer", value)
    if value_type is float:
        return ("float", value)
    if value_type is str:
        return ("string", value)
    if value_type is list:
        return ("array", tuple(_freeze_json_value(item) for item in value))
    if value_type is dict:
        return (
            "object",
            tuple((key, _freeze_json_value(value[key])) for key in sorted(value)),
        )
    raise TypeError("cache key contains a non-JSON value")


def load_scorecard(
    repository: GitRepository,
    scorecard_path: str,
    scorecard_ref: str,
) -> tuple[Scorecard, str]:
    try:
        normalized_path = validate_repository_path(scorecard_path)
    except ValueError as error:
        raise ProofStateError(ErrorCode.INVALID_ARGUMENT, str(error)) from error
    if (
        len(normalized_path.encode("utf-8", errors="surrogatepass")) + 1
        > ENTRY_PREFETCH_MAX_PATH_BYTES
    ):
        raise ProofStateError(
            ErrorCode.INVALID_ARGUMENT,
            "scorecard path must encode to fewer than 16 KiB",
        )
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
    machine_cache: dict[tuple[str, Hashable], EvidenceResult],
    artifact_cache: _ArtifactCache,
    attestation_material_cache: _AttestationCache,
    file_digest_cache: _FileDigestCache,
    test_symbol_cache: _TestSymbolCache,
    attestation_cache: dict[tuple[str, Hashable], EvidenceResult],
    work_budget: _EvaluationWorkBudget,
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
    evidence_results: list[EvidenceResult] = []
    for machine_evidence in assertion.evidence.machine:
        cache_key = (
            type(machine_evidence).__name__,
            _freeze_json_value(machine_evidence.model_dump(mode="json")),
        )
        if cache_key not in machine_cache:
            if isinstance(machine_evidence, FileEvidence):
                machine_cache[cache_key] = verify_machine_evidence(
                    machine_evidence,
                    repository,
                    scorecard.repository.commit,
                    scorecard.settings.max_evidence_bytes,
                    file_digest_cache=file_digest_cache,
                    work_budget=work_budget,
                )
            elif isinstance(machine_evidence, TestSymbolEvidence):
                machine_cache[cache_key] = verify_machine_evidence(
                    machine_evidence,
                    repository,
                    scorecard.repository.commit,
                    scorecard.settings.max_evidence_bytes,
                    test_symbol_cache=test_symbol_cache,
                    work_budget=work_budget,
                )
            else:
                machine_cache[cache_key] = verify_machine_evidence(
                    machine_evidence,
                    repository,
                    scorecard.repository.commit,
                    scorecard.settings.max_evidence_bytes,
                    artifact_cache=artifact_cache,
                    work_budget=work_budget,
                )
        evidence_results.append(deepcopy(machine_cache[cache_key]))
    for attestation_evidence in assertion.evidence.attestations:
        cache_key = (
            assertion.id,
            _freeze_json_value(attestation_evidence.model_dump(mode="json")),
        )
        if cache_key not in attestation_cache:
            attestation_cache[cache_key] = verify_attestation(
                attestation_evidence,
                repository,
                policy_commit,
                scorecard.repository.commit,
                scorecard.repository.identity,
                assertion.id,
                evaluated_at,
                scorecard.settings.max_evidence_bytes,
                cache=attestation_material_cache,
                work_budget=work_budget,
            )
        evidence_results.append(deepcopy(attestation_cache[cache_key]))
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

    evidence_paths: dict[str, list[str]] = {}
    for assertion in scorecard.assertions:
        evidence_paths.setdefault(scorecard.repository.commit, []).extend(
            evidence.path for evidence in assertion.evidence.machine
        )
        evidence_paths.setdefault(policy_commit, []).extend(
            evidence.path for evidence in assertion.evidence.attestations
        )
    for commit, paths in evidence_paths.items():
        repository.prefetch_entries(commit, paths)

    completed: dict[str, AssertionResult] = {}
    machine_cache: dict[tuple[str, Hashable], EvidenceResult] = {}
    artifact_cache = _ArtifactCache()
    attestation_material_cache = _AttestationCache()
    file_digest_cache: _FileDigestCache = {}
    test_symbol_cache: _TestSymbolCache = {}
    attestation_cache: dict[tuple[str, Hashable], EvidenceResult] = {}
    work_budget = _EvaluationWorkBudget()
    dependency_counts = {
        assertion.id: len(assertion.depends_on) for assertion in scorecard.assertions
    }
    dependents: dict[str, list[str]] = {assertion.id: [] for assertion in scorecard.assertions}
    ready = deque(
        assertion.id for assertion in scorecard.assertions if dependency_counts[assertion.id] == 0
    )
    by_id = {assertion.id: assertion for assertion in scorecard.assertions}
    for assertion in scorecard.assertions:
        for dependency in assertion.depends_on:
            dependents[dependency].append(assertion.id)
    while ready:
        assertion_id = ready.popleft()
        assertion = by_id[assertion_id]
        completed[assertion_id] = _evaluate_assertion(
            assertion,
            completed,
            repository,
            scorecard,
            policy_commit,
            instant,
            machine_cache,
            artifact_cache,
            attestation_material_cache,
            file_digest_cache,
            test_symbol_cache,
            attestation_cache,
            work_budget,
        )
        for dependent in dependents[assertion_id]:
            dependency_counts[dependent] -= 1
            if dependency_counts[dependent] == 0:
                ready.append(dependent)

    ordered_results = [completed[assertion.id] for assertion in scorecard.assertions]
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
