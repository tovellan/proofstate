from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from proofstate.errors import ErrorCode, ProofStateError
from proofstate.evidence import (
    EvidenceCode,
    EvidenceResult,
    _artifact_check_passes,
    _json_type,
    _resolve_pointer,
    verify_artifact,
    verify_attestation,
    verify_file,
    verify_machine_evidence,
    verify_test_symbol,
)
from proofstate.git import GitRepository
from proofstate.models import (
    ArtifactCheck,
    ArtifactEvidence,
    ArtifactOperator,
    AttestationEvidence,
    FileEvidence,
)
from proofstate.models import (
    TestSymbolEvidence as SymbolEvidence,
)


class BlobRepository:
    def __init__(self, content: bytes | Exception) -> None:
        self.content = content

    def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
        del commit, path, max_bytes
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


def repository_with(content: bytes | Exception) -> GitRepository:
    return cast(GitRepository, cast(Any, BlobRepository(content)))


def check(
    operator: ArtifactOperator, expected: Any = None, *, supplied: bool = True
) -> ArtifactCheck:
    values: dict[str, Any] = {"pointer": "", "operator": operator}
    if supplied:
        values["expected"] = expected
    return ArtifactCheck.model_validate(values)


def test_evidence_result_omits_empty_optional_fields() -> None:
    result = EvidenceResult("file", True, EvidenceCode.VERIFIED, "ok")
    assert result.to_dict() == {
        "type": "file",
        "passed": True,
        "code": "PSE000_VERIFIED",
        "message": "ok",
    }


@pytest.mark.parametrize(
    ("document", "pointer", "expected"),
    [
        ({"a/b": {"~key": 3}}, "/a~1b/~0key", 3),
        (["zero"], "/0", "zero"),
        (["zero"], "/x", None),
        (["zero"], "/2", None),
        ({"value": 1}, "/value/deeper", None),
        ({"value": 1}, "/missing", None),
        ({"value": 1}, "", {"value": 1}),
    ],
)
def test_json_pointer_resolution(document: Any, pointer: str, expected: Any) -> None:
    resolved = _resolve_pointer(document, pointer)
    if expected is None:
        assert type(resolved) is object
    else:
        assert resolved == expected


def test_oversized_json_pointer_index_is_missing() -> None:
    assert type(_resolve_pointer([], "/" + "9" * 5_000)) is object


@pytest.mark.parametrize(
    ("value", "operator", "expected", "passed"),
    [
        (1, ArtifactOperator.EQUALS, 1, True),
        (True, ArtifactOperator.EQUALS, 1, False),
        (1, ArtifactOperator.NOT_EQUALS, 2, True),
        ({"key": 1}, ArtifactOperator.CONTAINS, "key", True),
        ({"key": 1}, ArtifactOperator.CONTAINS, [], False),
        ({1: "value"}, ArtifactOperator.CONTAINS, True, False),
        ([1], ArtifactOperator.CONTAINS, True, False),
        ("ready", ArtifactOperator.CONTAINS, "ead", True),
        ("ready", ArtifactOperator.CONTAINS, 1, False),
        (7, ArtifactOperator.CONTAINS, 7, False),
        (7, ArtifactOperator.GREATER_THAN_OR_EQUAL, 7, True),
        (7, ArtifactOperator.LESS_THAN_OR_EQUAL, 8, True),
        (True, ArtifactOperator.GREATER_THAN_OR_EQUAL, 1, False),
        (None, ArtifactOperator.TYPE, "null", True),
        (True, ArtifactOperator.TYPE, "boolean", True),
        (1, ArtifactOperator.TYPE, "number", True),
        ("x", ArtifactOperator.TYPE, "string", True),
        ([], ArtifactOperator.TYPE, "array", True),
        ({}, ArtifactOperator.TYPE, "object", True),
    ],
)
def test_artifact_operator_semantics(
    value: Any,
    operator: ArtifactOperator,
    expected: Any,
    passed: bool,
) -> None:
    assert _artifact_check_passes(value, check(operator, expected)) is passed


def test_unsupported_json_type_is_explicit() -> None:
    assert _json_type(object()) == "unsupported"


def test_file_digest_mismatch_and_size_limit() -> None:
    mismatch = verify_file(
        FileEvidence(type="file", path="file.txt", sha256="0" * 64),
        repository_with(b"content"),
        "a" * 40,
        100,
    )
    oversized = verify_file(
        FileEvidence(type="file", path="file.txt"),
        repository_with(OverflowError()),
        "a" * 40,
        1,
    )
    assert mismatch.code == EvidenceCode.DIGEST_MISMATCH
    assert oversized.code == EvidenceCode.FILE_TOO_LARGE


def test_invalid_python_source_is_rejected() -> None:
    result = verify_test_symbol(
        SymbolEvidence(
            type="test_symbol",
            path="tests/test_invalid.py",
            symbol="test_invalid",
            framework="pytest",
        ),
        repository_with(b"def test_invalid(:\n"),
        "a" * 40,
        100,
    )
    assert result.code == EvidenceCode.TEST_PARSE_FAILED


def test_invalid_artifact_and_digest_are_rejected() -> None:
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "report.json",
            "format": "json",
            "checks": [{"pointer": "", "operator": "exists"}],
        }
    )
    invalid = verify_artifact(evidence, repository_with(b"{"), "a" * 40, 100)
    evidence_with_digest = evidence.model_copy(update={"sha256": "0" * 64})
    mismatch = verify_artifact(
        evidence_with_digest,
        repository_with(b"{}"),
        "a" * 40,
        100,
    )
    assert invalid.code == EvidenceCode.ARTIFACT_INVALID
    assert mismatch.code == EvidenceCode.DIGEST_MISMATCH


def test_attestation_invalid_future_digest_and_missing() -> None:
    evidence = AttestationEvidence(type="human_attestation", path="review.json")
    invalid = verify_attestation(
        evidence,
        repository_with(b"{}"),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        1_000,
    )
    future_document = (
        b'{"schema_version":"proofstate.dev/attestation/v1alpha1",'
        b'"identity":"reviewer@example.invalid",'
        b'"issued_at":"2027-01-01T00:00:00Z",'
        b'"expires_at":"2028-01-01T00:00:00Z",'
        b'"scope":{"repository":"example.invalid/repository",'
        b'"commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"assertions":["review"]},"statement":"Reviewed."}'
    )
    future = verify_attestation(
        evidence,
        repository_with(future_document),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        1_000,
    )
    mismatch = verify_attestation(
        evidence.model_copy(update={"sha256": "0" * 64}),
        repository_with(future_document),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        1_000,
    )
    missing = verify_attestation(
        evidence,
        repository_with(FileNotFoundError()),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        1_000,
    )
    assert invalid.code == EvidenceCode.ATTESTATION_INVALID
    assert future.code == EvidenceCode.ATTESTATION_NOT_YET_VALID
    assert mismatch.code == EvidenceCode.DIGEST_MISMATCH
    assert missing.code == EvidenceCode.FILE_MISSING


def test_git_failure_in_machine_evidence_fails_closed() -> None:
    result = verify_machine_evidence(
        FileEvidence(type="file", path="file.txt"),
        repository_with(ProofStateError(ErrorCode.GIT_COMMAND_FAILED, "failed")),
        "a" * 40,
        100,
    )
    assert result.code == EvidenceCode.INTERNAL_ERROR
