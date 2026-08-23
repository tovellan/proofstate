from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from proofstate.models import (
    ArtifactCheck,
    ArtifactOperator,
    Assertion,
    AttestationScope,
    EvidenceSet,
    FailureCap,
    HumanAttestation,
    Scorecard,
    Severity,
    parse_timestamp,
    validate_repository_path,
)
from proofstate.models import (
    TestSymbolEvidence as SymbolEvidence,
)


@given(st.lists(st.sampled_from(["..", ".", ".git"]), min_size=1).map("/".join))
def test_unsafe_repository_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        validate_repository_path(path)


@given(
    st.lists(
        st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
            min_size=1,
            max_size=10,
        ),
        min_size=1,
        max_size=5,
    )
)
def test_safe_repository_paths_round_trip(parts: list[str]) -> None:
    path = "/".join(parts)
    assert validate_repository_path(path) == path


@pytest.mark.parametrize("pointer", ["missing-slash", "/bad~2escape", "/bad~"])
def test_invalid_json_pointers_are_rejected(pointer: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactCheck(pointer=pointer, operator=ArtifactOperator.EXISTS)


def test_test_symbol_requires_pytest_name() -> None:
    with pytest.raises(ValidationError):
        SymbolEvidence(
            type="test_symbol",
            path="tests/check.py",
            symbol="helper",
            framework="pytest",
        )


@pytest.mark.parametrize("path", ["", "\\bad", "bad\x00path", "/absolute", ".git/config"])
def test_additional_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        validate_repository_path(path)


@pytest.mark.parametrize(
    "values",
    [
        {"pointer": "/x", "operator": "exists", "expected": True},
        {"pointer": "/x", "operator": "equals"},
        {"pointer": "/x", "operator": "type", "expected": "integer"},
    ],
)
def test_artifact_check_expected_value_contract(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ArtifactCheck.model_validate(values)


def test_empty_evidence_and_invalid_dependencies_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceSet()
    base = {
        "id": "item",
        "title": "Item",
        "severity": Severity.HIGH,
        "failure_cap": FailureCap.NONE,
        "evidence": {
            "machine": [{"type": "file", "path": "file.txt"}],
            "attestations": [],
        },
    }
    with pytest.raises(ValidationError):
        Assertion.model_validate({**base, "depends_on": ["item"]})
    with pytest.raises(ValidationError):
        Assertion.model_validate({**base, "depends_on": ["other", "other"]})


def test_scorecard_rejects_duplicate_and_unknown_assertions() -> None:
    assertion = {
        "id": "item",
        "title": "Item",
        "severity": "high",
        "failure_cap": "none",
        "depends_on": [],
        "evidence": {"machine": [{"type": "file", "path": "file.txt"}]},
    }
    base = {
        "schema_version": "proofstate.dev/scorecard/v1alpha1",
        "repository": {"identity": "example.invalid/repo", "commit": "a" * 40},
    }
    with pytest.raises(ValidationError):
        Scorecard.model_validate({**base, "assertions": [assertion, assertion]})
    unknown = {**assertion, "depends_on": ["absent"]}
    with pytest.raises(ValidationError):
        Scorecard.model_validate({**base, "assertions": [unknown]})


def test_timestamp_and_attestation_validators() -> None:
    aware = parse_timestamp("2026-01-01T00:00:00Z")
    assert parse_timestamp(aware) == aware
    with pytest.raises(ValueError):
        parse_timestamp(123)
    with pytest.raises(ValueError):
        parse_timestamp("2026-01-01T00:00:00")
    with pytest.raises(ValidationError):
        AttestationScope(
            repository="example.invalid/repo",
            commit="a" * 40,
            assertions=["review", "review"],
        )
    with pytest.raises(ValidationError):
        HumanAttestation.model_validate(
            {
                "schema_version": "proofstate.dev/attestation/v1alpha1",
                "identity": "reviewer@example.invalid",
                "issued_at": "2026-02-01T00:00:00Z",
                "expires_at": "2026-01-01T00:00:00Z",
                "scope": {
                    "repository": "example.invalid/repo",
                    "commit": "a" * 40,
                    "assertions": ["review"],
                },
                "statement": "Reviewed.",
            }
        )
