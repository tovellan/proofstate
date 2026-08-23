from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from proofstate.cli import main
from tests.conftest import RepositoryFixture

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def test_cli_emits_machine_readable_result(
    repository_fixture: RepositoryFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "check",
                ".proofstate/scorecard.yaml",
                "--repo",
                str(repository_fixture.root),
                "--at",
                NOW.isoformat(),
                "--format",
                "json",
            ]
        )

    assert stopped.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["repository"]["evidence_commit"] == repository_fixture.target_commit


def test_cli_schema_is_json(capsys: pytest.CaptureFixture[str]) -> None:
    main(["schema"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["title"] == "Scorecard"


def test_cli_validation_error_uses_exit_two(
    repository_fixture: RepositoryFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "check",
                "missing.yaml",
                "--repo",
                str(repository_fixture.root),
                "--format",
                "json",
            ]
        )

    assert stopped.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "PS004_SCORECARD_NOT_FOUND"
