from __future__ import annotations

from typing import Any

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


def dependency_chain_scorecard(size: int, *, cycle: bool = False) -> dict[str, Any]:
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
                "depends_on": dependencies,
                "evidence": {"machine": [{"type": "file", "path": "evidence.txt"}]},
            }
        )
    return {
        "schema_version": "proofstate.dev/scorecard/v1alpha1",
        "repository": {"identity": "example.invalid/repo", "commit": "a" * 40},
        "assertions": assertions,
    }


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


@pytest.mark.parametrize(
    "symbol",
    ["helper", "Helper.test_hidden", "TestRelease.helper", "TestRelease.test_ok.nested"],
)
def test_test_symbol_requires_collectable_pytest_name(symbol: str) -> None:
    with pytest.raises(ValidationError):
        SymbolEvidence(
            type="test_symbol",
            path="tests/check.py",
            symbol=symbol,
            framework="pytest",
        )


@pytest.mark.parametrize("symbol", ["test_release", "TestRelease.test_candidate"])
def test_collectable_pytest_names_are_accepted(symbol: str) -> None:
    evidence = SymbolEvidence(
        type="test_symbol",
        path="tests/check.py",
        symbol=symbol,
        framework="pytest",
    )
    assert evidence.symbol == symbol


@pytest.mark.parametrize(
    "path",
    [
        "",
        "\\bad",
        "bad\x00path",
        "/absolute",
        ".git/config",
        "./file",
        "a/./b",
        "a//b",
        "a/",
    ],
)
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


@pytest.mark.parametrize(
    "expected",
    [object(), ("tuple",), {1: "non-string key"}, {"nested": [object()]}],
)
def test_artifact_check_rejects_non_json_expected_values(expected: object) -> None:
    with pytest.raises(ValidationError):
        ArtifactCheck.model_validate(
            {"pointer": "/x", "operator": ArtifactOperator.EQUALS, "expected": expected}
        )


@pytest.mark.parametrize(
    "expected",
    [float("inf"), float("-inf"), float("nan"), {"nested": [float("inf")]}],
)
def test_artifact_check_rejects_non_finite_expected_values(expected: object) -> None:
    with pytest.raises(ValidationError):
        ArtifactCheck.model_validate(
            {"pointer": "/x", "operator": ArtifactOperator.EQUALS, "expected": expected}
        )


@pytest.mark.parametrize(
    "expected",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "1e400",
        '{"nested":[NaN]}',
        '{"nested":[-1e400]}',
    ],
)
def test_artifact_check_json_input_rejects_non_finite_expected_values(expected: str) -> None:
    payload = f'{{"pointer":"/x","operator":"equals","expected":{expected}}}'

    with pytest.raises(ValidationError, match="finite JSON numbers"):
        ArtifactCheck.model_validate_json(payload)


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        (ArtifactOperator.GREATER_THAN_OR_EQUAL, True),
        (ArtifactOperator.GREATER_THAN_OR_EQUAL, "1"),
        (ArtifactOperator.LESS_THAN_OR_EQUAL, None),
        (ArtifactOperator.LESS_THAN_OR_EQUAL, [1]),
    ],
)
def test_numeric_artifact_checks_require_json_numbers(
    operator: ArtifactOperator, expected: object
) -> None:
    with pytest.raises(ValidationError, match="expected must be a JSON number"):
        ArtifactCheck.model_validate({"pointer": "/x", "operator": operator, "expected": expected})


@pytest.mark.parametrize("expected", [["number"], {"type": "number"}])
def test_type_artifact_checks_reject_non_string_values(expected: object) -> None:
    with pytest.raises(ValidationError, match="type expected must be a JSON type name"):
        ArtifactCheck.model_validate(
            {"pointer": "/x", "operator": ArtifactOperator.TYPE, "expected": expected}
        )


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


def test_maximum_dependency_chain_validates_without_recursion() -> None:
    scorecard = Scorecard.model_validate(dependency_chain_scorecard(1_000))

    assert len(scorecard.assertions) == 1_000


def test_maximum_dependency_cycle_is_a_validation_error() -> None:
    with pytest.raises(ValidationError, match="dependency graph contains a cycle"):
        Scorecard.model_validate(dependency_chain_scorecard(1_000, cycle=True))


def test_timestamp_and_attestation_validators() -> None:
    aware = parse_timestamp("2026-01-01T00:00:00Z")
    assert parse_timestamp(aware) == aware
    with pytest.raises(ValueError):
        parse_timestamp(123)
    with pytest.raises(ValueError):
        parse_timestamp("2026-01-01T00:00:00")
    with pytest.raises(ValueError):
        parse_timestamp("2026-01-01 00:00:00+00:00")
    with pytest.raises(ValueError):
        parse_timestamp("2026-W01-4T00:00:00+00:00")
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
