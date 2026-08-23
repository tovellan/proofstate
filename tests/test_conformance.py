from __future__ import annotations

import json
import shutil
from pathlib import Path

from proofstate.conformance import run_conformance


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
