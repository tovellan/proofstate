from __future__ import annotations

from datetime import UTC, datetime

from proofstate.evidence import verify_artifact, verify_test_symbol
from proofstate.git import GitRepository
from proofstate.models import ArtifactEvidence
from proofstate.models import TestSymbolEvidence as SymbolEvidence
from tests.conftest import RepositoryFixture

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def test_top_level_test_symbol_is_found(repository_fixture: RepositoryFixture) -> None:
    evidence = SymbolEvidence(
        type="test_symbol",
        path="tests/test_widget.py",
        symbol="test_widget",
        framework="pytest",
    )

    result = verify_test_symbol(
        evidence,
        GitRepository(repository_fixture.root),
        repository_fixture.target_commit,
        1_048_576,
    )

    assert result.passed


def test_missing_test_symbol_fails(repository_fixture: RepositoryFixture) -> None:
    evidence = SymbolEvidence(
        type="test_symbol",
        path="tests/test_widget.py",
        symbol="test_absent",
        framework="pytest",
    )

    result = verify_test_symbol(
        evidence,
        GitRepository(repository_fixture.root),
        repository_fixture.target_commit,
        1_048_576,
    )

    assert result.code == "PSE202_TEST_SYMBOL_MISSING"


def test_artifact_pointer_failure_reports_only_indexes(
    repository_fixture: RepositoryFixture,
) -> None:
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "evidence/report.json",
            "format": "json",
            "checks": [
                {"pointer": "/missing", "operator": "exists"},
                {"pointer": "/tests/failed", "operator": "not_equals", "expected": 0},
                {"pointer": "/tests/passed", "operator": "lte", "expected": 5},
            ],
        }
    )

    result = verify_artifact(
        evidence,
        GitRepository(repository_fixture.root),
        repository_fixture.target_commit,
        1_048_576,
    )

    assert result.code == "PSE302_ARTIFACT_CHECK_FAILED"
    assert result.details == {"failed_check_indexes": [0, 1, 2]}
