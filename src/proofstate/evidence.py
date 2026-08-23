"""Evidence verification against immutable Git objects."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from proofstate.document import DocumentError, load_document
from proofstate.errors import ProofStateError
from proofstate.git import GitRepository
from proofstate.models import (
    ArtifactCheck,
    ArtifactEvidence,
    ArtifactOperator,
    AttestationEvidence,
    FileEvidence,
    HumanAttestation,
    MachineEvidence,
    TestSymbolEvidence,
)


class EvidenceCode(StrEnum):
    VERIFIED = "PSE000_VERIFIED"
    DEPENDENCY_FAILED = "PSE001_DEPENDENCY_FAILED"
    FILE_MISSING = "PSE101_FILE_MISSING"
    FILE_TOO_LARGE = "PSE102_FILE_TOO_LARGE"
    DIGEST_MISMATCH = "PSE103_DIGEST_MISMATCH"
    TEST_PARSE_FAILED = "PSE201_TEST_PARSE_FAILED"
    TEST_SYMBOL_MISSING = "PSE202_TEST_SYMBOL_MISSING"
    ARTIFACT_INVALID = "PSE301_ARTIFACT_INVALID"
    ARTIFACT_CHECK_FAILED = "PSE302_ARTIFACT_CHECK_FAILED"
    ATTESTATION_INVALID = "PSE401_ATTESTATION_INVALID"
    ATTESTATION_NOT_YET_VALID = "PSE402_ATTESTATION_NOT_YET_VALID"
    ATTESTATION_EXPIRED = "PSE403_ATTESTATION_EXPIRED"
    ATTESTATION_SCOPE_MISMATCH = "PSE404_ATTESTATION_SCOPE_MISMATCH"
    INTERNAL_ERROR = "PSE900_INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    evidence_type: str
    passed: bool
    code: EvidenceCode
    message: str
    path: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.evidence_type,
            "passed": self.passed,
            "code": self.code.value,
            "message": self.message,
        }
        if self.path is not None:
            result["path"] = self.path
        if self.details:
            result["details"] = self.details
        return result


def _digest_matches(content: bytes, expected: str | None) -> bool:
    return expected is None or hashlib.sha256(content).hexdigest() == expected


def _read_evidence_blob(
    repository: GitRepository,
    commit: str,
    path: str,
    max_bytes: int,
    evidence_type: str,
) -> tuple[bytes | None, EvidenceResult | None]:
    try:
        return repository.read_blob(commit, path, max_bytes=max_bytes), None
    except FileNotFoundError:
        return None, EvidenceResult(
            evidence_type,
            False,
            EvidenceCode.FILE_MISSING,
            "evidence is absent or is not a regular file in the pinned tree",
            path,
        )
    except OverflowError:
        return None, EvidenceResult(
            evidence_type,
            False,
            EvidenceCode.FILE_TOO_LARGE,
            "evidence exceeds the configured byte limit",
            path,
        )


def verify_file(
    evidence: FileEvidence,
    repository: GitRepository,
    commit: str,
    max_bytes: int,
) -> EvidenceResult:
    content, error = _read_evidence_blob(
        repository, commit, evidence.path, max_bytes, evidence.type
    )
    if error is not None:
        return error
    assert content is not None
    if not _digest_matches(content, evidence.sha256):
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.DIGEST_MISMATCH,
            "file digest does not match the scorecard",
            evidence.path,
        )
    return EvidenceResult(
        evidence.type,
        True,
        EvidenceCode.VERIFIED,
        "regular file exists in the pinned tree",
        evidence.path,
        {"sha256": hashlib.sha256(content).hexdigest()},
    )


def _collect_pytest_symbols(tree: ast.Module) -> set[str]:
    symbols: set[str] = set()
    function_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, function_types):
            symbols.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, function_types):
                    symbols.add(f"{node.name}.{child.name}")
    return symbols


def verify_test_symbol(
    evidence: TestSymbolEvidence,
    repository: GitRepository,
    commit: str,
    max_bytes: int,
) -> EvidenceResult:
    content, error = _read_evidence_blob(
        repository, commit, evidence.path, max_bytes, evidence.type
    )
    if error is not None:
        return error
    assert content is not None
    try:
        tree = ast.parse(content, filename=evidence.path)
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.TEST_PARSE_FAILED,
            "test file is not valid Python source",
            evidence.path,
        )
    symbols = _collect_pytest_symbols(tree)
    if evidence.symbol not in symbols:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.TEST_SYMBOL_MISSING,
            "named pytest symbol is missing from the pinned tree",
            evidence.path,
            {"symbol": evidence.symbol},
        )
    return EvidenceResult(
        evidence.type,
        True,
        EvidenceCode.VERIFIED,
        "named pytest symbol exists in the pinned tree",
        evidence.path,
        {"symbol": evidence.symbol},
    )


_MISSING = object()


def _resolve_pointer(document: Any, pointer: str) -> Any:
    current = document
    if not pointer:
        return current
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                return _MISSING
            try:
                index = int(token)
            except ValueError:
                return _MISSING
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unsupported"


def _json_values_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return False
        return all(
            _json_values_equal(left_value, right[left_key]) for left_key, left_value in left.items()
        )
    return bool(left == right)


def _artifact_check_passes(value: Any, check: ArtifactCheck) -> bool:
    if check.operator == ArtifactOperator.EXISTS:
        return value is not _MISSING
    if value is _MISSING:
        return False
    if check.operator == ArtifactOperator.EQUALS:
        return _json_values_equal(value, check.expected)
    if check.operator == ArtifactOperator.NOT_EQUALS:
        return not _json_values_equal(value, check.expected)
    if check.operator == ArtifactOperator.CONTAINS:
        if isinstance(value, dict):
            return any(_json_values_equal(item, check.expected) for item in value)
        if isinstance(value, list):
            return any(_json_values_equal(item, check.expected) for item in value)
        if isinstance(value, str):
            return isinstance(check.expected, str) and check.expected in value
        return False
    if check.operator in {
        ArtifactOperator.GREATER_THAN_OR_EQUAL,
        ArtifactOperator.LESS_THAN_OR_EQUAL,
    }:
        if (
            isinstance(value, bool)
            or isinstance(check.expected, bool)
            or not isinstance(value, (int, float))
            or not isinstance(check.expected, (int, float))
        ):
            return False
        if check.operator == ArtifactOperator.GREATER_THAN_OR_EQUAL:
            return bool(value >= check.expected)
        return bool(value <= check.expected)
    if check.operator == ArtifactOperator.TYPE:
        return bool(_json_type(value) == check.expected)
    return False


def verify_artifact(
    evidence: ArtifactEvidence,
    repository: GitRepository,
    commit: str,
    max_bytes: int,
) -> EvidenceResult:
    content, error = _read_evidence_blob(
        repository, commit, evidence.path, max_bytes, evidence.type
    )
    if error is not None:
        return error
    assert content is not None
    if not _digest_matches(content, evidence.sha256):
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.DIGEST_MISMATCH,
            "artifact digest does not match the scorecard",
            evidence.path,
        )
    try:
        document = load_document(content, format_hint=evidence.format)
    except DocumentError:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ARTIFACT_INVALID,
            "artifact is not valid bounded structured data",
            evidence.path,
        )
    failures = [
        index
        for index, check in enumerate(evidence.checks)
        if not _artifact_check_passes(_resolve_pointer(document, check.pointer), check)
    ]
    if failures:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ARTIFACT_CHECK_FAILED,
            "one or more structured artifact checks failed",
            evidence.path,
            {"failed_check_indexes": failures},
        )
    return EvidenceResult(
        evidence.type,
        True,
        EvidenceCode.VERIFIED,
        "all structured artifact checks passed",
        evidence.path,
        {"checks": len(evidence.checks), "sha256": hashlib.sha256(content).hexdigest()},
    )


def verify_attestation(
    evidence: AttestationEvidence,
    repository: GitRepository,
    policy_commit: str,
    target_commit: str,
    repository_identity: str,
    assertion_id: str,
    evaluated_at: datetime,
    max_bytes: int,
) -> EvidenceResult:
    try:
        content, error = _read_evidence_blob(
            repository, policy_commit, evidence.path, max_bytes, evidence.type
        )
    except ProofStateError:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.INTERNAL_ERROR,
            "Git object verification failed closed",
            evidence.path,
        )
    if error is not None:
        return error
    assert content is not None
    if not _digest_matches(content, evidence.sha256):
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.DIGEST_MISMATCH,
            "attestation digest does not match the scorecard",
            evidence.path,
        )
    try:
        document = load_document(content)
        attestation = HumanAttestation.model_validate(document)
    except (DocumentError, ValidationError):
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ATTESTATION_INVALID,
            "attestation does not conform to the versioned schema",
            evidence.path,
        )
    if evaluated_at < attestation.issued_at:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ATTESTATION_NOT_YET_VALID,
            "attestation issue time is in the future",
            evidence.path,
        )
    if evaluated_at >= attestation.expires_at:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ATTESTATION_EXPIRED,
            "attestation has expired",
            evidence.path,
        )
    scope = attestation.scope
    if (
        scope.repository != repository_identity
        or scope.commit != target_commit
        or assertion_id not in scope.assertions
    ):
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ATTESTATION_SCOPE_MISMATCH,
            "attestation does not cover this repository, commit, and assertion",
            evidence.path,
        )
    return EvidenceResult(
        evidence.type,
        True,
        EvidenceCode.VERIFIED,
        "human attestation is current and correctly scoped",
        evidence.path,
        {
            "identity": attestation.identity,
            "issued_at": attestation.issued_at.isoformat(),
            "expires_at": attestation.expires_at.isoformat(),
        },
    )


def verify_machine_evidence(
    evidence: MachineEvidence,
    repository: GitRepository,
    commit: str,
    max_bytes: int,
) -> EvidenceResult:
    try:
        if isinstance(evidence, FileEvidence):
            return verify_file(evidence, repository, commit, max_bytes)
        if isinstance(evidence, TestSymbolEvidence):
            return verify_test_symbol(evidence, repository, commit, max_bytes)
        return verify_artifact(evidence, repository, commit, max_bytes)
    except ProofStateError:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.INTERNAL_ERROR,
            "Git object verification failed closed",
            evidence.path,
        )
