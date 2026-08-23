from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from proofstate.conformance import export_conformance, run_conformance


def fixture_root() -> Path:
    return Path(__file__).parents[1] / "src" / "proofstate" / "fixtures" / "v1alpha1"


def test_installed_conformance_bundle_passes() -> None:
    result = run_conformance(fixture_root())

    assert result.passed is True
    assert len(result.cases) == 10
    assert {case.observed for case in result.cases} == {
        "valid",
        "invalid_document",
        "invalid_scorecard",
        "invalid_attestation",
    }


def test_conformance_bundle_fails_on_tampered_fixture(tmp_path: Path) -> None:
    copied = tmp_path / "fixtures"
    shutil.copytree(fixture_root(), copied)
    target = copied / "attestation-valid.json"
    target.write_text(target.read_text() + "\n", encoding="utf-8")

    result = run_conformance(copied)

    tampered = next(case for case in result.cases if case.case_id == "attestation-valid")
    assert result.passed is False
    assert tampered.observed == "digest_mismatch"


def test_conformance_bundle_fails_cleanly_on_invalid_manifest(tmp_path: Path) -> None:
    copied = tmp_path / "fixtures"
    shutil.copytree(fixture_root(), copied)
    (copied / "manifest.json").write_text("{}", encoding="utf-8")

    result = run_conformance(copied)

    assert result.passed is False
    assert result.cases[0].observed == "invalid_manifest"


def test_conformance_result_is_portable_json() -> None:
    payload = json.loads(json.dumps(run_conformance(fixture_root()).to_dict()))

    assert payload["schema_version"] == "proofstate.dev/conformance-result/v1alpha1"
    assert payload["fixture_schema_version"] == ("proofstate.dev/conformance-manifest/v1alpha1")


def test_conformance_export_preserves_exact_verified_files(tmp_path: Path) -> None:
    destination = tmp_path / "exported"

    result = export_conformance(destination, fixture_root())

    assert result.passed is True
    assert run_conformance(destination).passed is True
    assert {path.name for path in destination.iterdir()} == {
        path.name for path in fixture_root().iterdir()
    }
    for source in fixture_root().iterdir():
        assert (destination / source.name).read_bytes() == source.read_bytes()


def test_conformance_export_refuses_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    marker = destination / "preserved.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        export_conformance(destination, fixture_root())

    assert marker.read_text(encoding="utf-8") == "keep"
