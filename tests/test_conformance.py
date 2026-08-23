from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from proofstate.conformance import (
    CONFORMANCE_MAX_BYTES,
    _read_bounded,
    export_conformance,
    run_conformance,
)


def fixture_root() -> Path:
    return Path(__file__).parents[1] / "src" / "proofstate" / "fixtures" / "v1alpha1"


def test_installed_conformance_bundle_passes() -> None:
    result = run_conformance(fixture_root())

    assert result.passed is True
    assert len(result.cases) == 17
    assert {case.observed for case in result.cases} == {
        "valid",
        "invalid_document",
        "invalid_scorecard",
        "invalid_attestation",
    }


def test_yaml_core_conformance_cases_have_declared_outcomes() -> None:
    result = run_conformance(fixture_root())
    yaml_cases = {
        case.case_id: (case.expected, case.observed)
        for case in result.cases
        if case.case_id.startswith("scorecard-yaml-")
    }

    assert yaml_cases == {
        "scorecard-yaml-core-scalars": ("valid", "valid"),
        "scorecard-yaml-legacy-binary": ("invalid_scorecard", "invalid_scorecard"),
        "scorecard-yaml-legacy-separator": ("invalid_scorecard", "invalid_scorecard"),
        "scorecard-yaml-legacy-sexagesimal": ("invalid_scorecard", "invalid_scorecard"),
        "scorecard-yaml-merge-key": ("invalid_document", "invalid_document"),
        "scorecard-yaml-nonfinite": ("invalid_document", "invalid_document"),
        "scorecard-yaml-nonstring-key": ("invalid_document", "invalid_document"),
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


def test_conformance_bundle_normalizes_fixture_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from proofstate import conformance as conformance_module

    original_read = conformance_module._read_bounded

    def permission_failure(path: Any) -> bytes:
        if path.name == "attestation-valid.json":
            raise PermissionError("denied")
        return original_read(path)

    monkeypatch.setattr(conformance_module, "_read_bounded", permission_failure)

    result = run_conformance(fixture_root())

    failed = next(case for case in result.cases if case.case_id == "attestation-valid")
    assert result.passed is False
    assert failed.observed == "fixture_unavailable"


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


def test_conformance_read_stops_at_limit_plus_one() -> None:
    class GuardedStream(io.BytesIO):
        def __init__(self, content: bytes) -> None:
            super().__init__(content)
            self.requests: list[int] = []
            self.returned = 0

        def read(self, size: int | None = -1) -> bytes:
            if size is None or size < 0:
                raise AssertionError("unbounded reads are not allowed")
            self.requests.append(size)
            content = super().read(size)
            self.returned += len(content)
            return content

    class GuardedResource:
        def __init__(self, content: bytes) -> None:
            self.content = content
            self.stream: GuardedStream | None = None

        def open(self, mode: str) -> GuardedStream:
            assert mode == "rb"
            self.stream = GuardedStream(self.content)
            return self.stream

        def read_bytes(self) -> bytes:
            raise AssertionError("read_bytes would load the unbounded resource")

    resource = GuardedResource(b"x" * (CONFORMANCE_MAX_BYTES + 2))

    with pytest.raises(ValueError, match="exceeds the one MiB limit"):
        _read_bounded(cast(Any, resource))

    assert resource.stream is not None
    assert resource.stream.requests == [CONFORMANCE_MAX_BYTES + 1]
    assert resource.stream.returned == CONFORMANCE_MAX_BYTES + 1
