from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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


def test_cli_attestation_schema_is_json(capsys: pytest.CaptureFixture[str]) -> None:
    main(["schema", "attestation"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["title"] == "HumanAttestation"


def test_cli_conformance_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    main(["conformance", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert len(payload["cases"]) == 10


def test_cli_exports_conformance_bundle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    destination = tmp_path / "conformance"

    main(["conformance", "--export", str(destination), "--format", "json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert (destination / "manifest.json").is_file()


def test_cli_export_refuses_existing_destination(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()

    with pytest.raises(SystemExit) as stopped:
        main(["conformance", "--export", str(destination), "--format", "json"])

    assert stopped.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "PS012_CONFORMANCE_EXPORT_FAILED"


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
