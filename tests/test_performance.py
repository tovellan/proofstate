from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from proofstate.evaluate import evaluate_scorecard
from tests.conftest import RepositoryFixture


@pytest.mark.performance
def test_one_hundred_file_assertions_complete_within_ten_seconds(
    repository_fixture: RepositoryFixture,
) -> None:
    scorecard = repository_fixture.copy_scorecard()
    template = scorecard["assertions"][0]
    scorecard["assertions"] = []
    for index in range(100):
        assertion = {
            **template,
            "id": f"source-{index}",
            "depends_on": [],
        }
        scorecard["assertions"].append(assertion)
    repository_fixture.commit_policy(scorecard)

    started = time.perf_counter()
    result = evaluate_scorecard(
        ".proofstate/scorecard.yaml",
        repository_path=repository_fixture.root,
        evaluated_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    elapsed = time.perf_counter() - started

    assert result.passed
    assert elapsed < 10
