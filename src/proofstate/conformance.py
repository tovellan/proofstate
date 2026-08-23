"""Installed conformance fixtures for independent v1alpha1 implementations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from proofstate.document import DocumentError, load_document
from proofstate.models import HumanAttestation, Identifier, Scorecard, Sha256

CONFORMANCE_MAX_BYTES = 1_048_576
CONFORMANCE_SCHEMA_VERSION = "proofstate.dev/conformance-manifest/v1alpha1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Case(_StrictModel):
    id: Identifier
    document_kind: Literal["scorecard", "attestation"]
    path: str
    sha256: Sha256
    expected: Literal[
        "valid",
        "invalid_document",
        "invalid_scorecard",
        "invalid_attestation",
    ]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {"", ".", ".."}:
            raise ValueError("fixture path must be a single filename")
        return value


class _Manifest(_StrictModel):
    schema_version: Literal["proofstate.dev/conformance-manifest/v1alpha1"]
    cases: list[_Case] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_cases(self) -> _Manifest:
        ids = [case.id for case in self.cases]
        paths = [case.path for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("conformance case ids must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("conformance fixture paths must be unique")
        return self


@dataclass(frozen=True, slots=True)
class ConformanceCaseResult:
    case_id: str
    expected: str
    observed: str
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "expected": self.expected,
            "observed": self.observed,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class ConformanceResult:
    passed: bool
    schema_version: str
    cases: list[ConformanceCaseResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "proofstate.dev/conformance-result/v1alpha1",
            "passed": self.passed,
            "fixture_schema_version": self.schema_version,
            "cases": [case.to_dict() for case in self.cases],
        }


def _fixture_root() -> Traversable:
    return files("proofstate").joinpath("fixtures", "v1alpha1")


def _read_bounded(path: Traversable) -> bytes:
    with path.open("rb") as source:
        content = source.read(CONFORMANCE_MAX_BYTES + 1)
    if len(content) > CONFORMANCE_MAX_BYTES:
        raise ValueError("conformance file exceeds the one MiB limit")
    return content


def _load_manifest(root: Traversable) -> _Manifest:
    content = _read_bounded(root.joinpath("manifest.json"))
    try:
        return _Manifest.model_validate(load_document(content, format_hint="json"))
    except (DocumentError, ValidationError) as error:
        raise ValueError("installed conformance manifest is invalid") from error


def _observe(document_kind: str, content: bytes) -> str:
    try:
        document = load_document(content)
    except DocumentError:
        return "invalid_document"
    try:
        if document_kind == "scorecard":
            Scorecard.model_validate(document)
        else:
            HumanAttestation.model_validate(document)
    except ValidationError:
        return f"invalid_{document_kind}"
    return "valid"


def run_conformance(root: Traversable | None = None) -> ConformanceResult:
    """Validate the installed fixture corpus and return every case outcome."""

    fixture_root = root or _fixture_root()
    try:
        manifest = _load_manifest(fixture_root)
    except (OSError, ValueError):
        failed_manifest = ConformanceCaseResult(
            case_id="manifest",
            expected="valid",
            observed="invalid_manifest",
            passed=False,
        )
        return ConformanceResult(
            passed=False,
            schema_version=CONFORMANCE_SCHEMA_VERSION,
            cases=[failed_manifest],
        )
    results: list[ConformanceCaseResult] = []
    for case in manifest.cases:
        try:
            content = _read_bounded(fixture_root.joinpath(case.path))
        except (OSError, ValueError):
            observed = "fixture_unavailable"
        else:
            if hashlib.sha256(content).hexdigest() != case.sha256:
                observed = "digest_mismatch"
            else:
                observed = _observe(case.document_kind, content)
        results.append(
            ConformanceCaseResult(
                case_id=case.id,
                expected=case.expected,
                observed=observed,
                passed=observed == case.expected,
            )
        )
    return ConformanceResult(
        passed=all(case.passed for case in results),
        schema_version=manifest.schema_version,
        cases=results,
    )


def export_conformance(
    destination: Path,
    root: Traversable | None = None,
) -> ConformanceResult:
    """Write the exact verified corpus to a new destination directory."""

    fixture_root = root or _fixture_root()
    result = run_conformance(fixture_root)
    if not result.passed:
        raise ValueError("conformance bundle must pass before export")
    manifest = _load_manifest(fixture_root)
    payloads = {"manifest.json": _read_bounded(fixture_root.joinpath("manifest.json"))}
    for case in manifest.cases:
        content = _read_bounded(fixture_root.joinpath(case.path))
        if hashlib.sha256(content).hexdigest() != case.sha256:
            raise ValueError("conformance fixture changed during export")
        payloads[case.path] = content
    if destination.exists():
        raise FileExistsError("conformance export destination already exists")
    if not destination.parent.is_dir():
        raise FileNotFoundError("conformance export parent directory does not exist")
    destination.mkdir(mode=0o755)
    for name, content in sorted(payloads.items()):
        with (destination / name).open("xb") as target:
            target.write(content)
    return result
