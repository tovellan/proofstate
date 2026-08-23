"""Versioned scorecard and attestation models."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9._-]{0,63}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitObjectId = Annotated[str, StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def validate_repository_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("path must be a non-empty POSIX repository path")
    path = PurePosixPath(value)
    parts = value.split("/")
    if path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path must be relative and cannot contain dot segments")
    if parts[0] == ".git":
        raise ValueError("paths inside .git are not evidence")
    return value


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GateLevel(StrEnum):
    NONE = "none"
    ADVISORY = "advisory"
    MERGE = "merge"
    RELEASE = "release"


class FailureCap(StrEnum):
    NONE = "none"
    ADVISORY = "advisory"
    MERGE = "merge"


class FileEvidence(StrictModel):
    type: Literal["file"]
    path: str
    sha256: Sha256 | None = None

    _path = field_validator("path")(validate_repository_path)


class TestSymbolEvidence(StrictModel):
    type: Literal["test_symbol"]
    path: str
    symbol: str
    framework: Literal["pytest"] = "pytest"

    _path = field_validator("path")(validate_repository_path)

    @field_validator("path")
    @classmethod
    def require_python_file(cls, value: str) -> str:
        if not value.endswith(".py"):
            raise ValueError("pytest symbol evidence requires a .py file")
        return value

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", value):
            raise ValueError("symbol must be a dotted Python identifier")
        parts = value.split(".")
        top_level_test = len(parts) == 1 and parts[0].startswith("test_")
        test_method = (
            len(parts) == 2 and parts[0].startswith("Test") and parts[1].startswith("test_")
        )
        if not (top_level_test or test_method):
            raise ValueError("pytest symbol must name a test function or Test class test method")
        return value


class ArtifactOperator(StrEnum):
    EXISTS = "exists"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN_OR_EQUAL = "lte"
    TYPE = "type"


class ArtifactCheck(StrictModel):
    pointer: str
    operator: Annotated[ArtifactOperator, Field(strict=False)]
    expected: Any = None

    @field_validator("pointer")
    @classmethod
    def validate_pointer(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("pointer must be an RFC 6901 JSON Pointer")
        for token in value.split("/")[1:]:
            if re.search(r"~(?![01])", token):
                raise ValueError("pointer contains an invalid escape")
        return value

    @model_validator(mode="after")
    def validate_expected(self) -> ArtifactCheck:
        supplied = "expected" in self.model_fields_set
        if self.operator == ArtifactOperator.EXISTS and supplied:
            raise ValueError("exists checks cannot set expected")
        if self.operator != ArtifactOperator.EXISTS and not supplied:
            raise ValueError(f"{self.operator.value} checks require expected")
        if self.operator == ArtifactOperator.TYPE and self.expected not in {
            "null",
            "boolean",
            "number",
            "string",
            "array",
            "object",
        }:
            raise ValueError("type expected must be a JSON type name")
        return self


class ArtifactEvidence(StrictModel):
    type: Literal["artifact"]
    path: str
    format: Literal["json", "yaml"]
    checks: list[ArtifactCheck] = Field(min_length=1, max_length=100)
    sha256: Sha256 | None = None

    _path = field_validator("path")(validate_repository_path)


MachineEvidence = Annotated[
    FileEvidence | TestSymbolEvidence | ArtifactEvidence,
    Field(discriminator="type"),
]


class AttestationEvidence(StrictModel):
    type: Literal["human_attestation"]
    path: str
    sha256: Sha256 | None = None

    _path = field_validator("path")(validate_repository_path)


class EvidenceSet(StrictModel):
    machine: list[MachineEvidence] = Field(default_factory=list, max_length=100)
    attestations: list[AttestationEvidence] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_evidence(self) -> EvidenceSet:
        if not self.machine and not self.attestations:
            raise ValueError("an assertion requires machine evidence or an attestation")
        return self


class Assertion(StrictModel):
    id: Identifier
    title: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    severity: Annotated[Severity, Field(strict=False)]
    failure_cap: Annotated[FailureCap, Field(strict=False)] = FailureCap.NONE
    depends_on: list[Identifier] = Field(default_factory=list, max_length=100)
    evidence: EvidenceSet

    @model_validator(mode="after")
    def reject_self_dependency(self) -> Assertion:
        if self.id in self.depends_on:
            raise ValueError("an assertion cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("depends_on cannot contain duplicates")
        return self


class RepositoryTarget(StrictModel):
    identity: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    commit: GitObjectId


class Settings(StrictModel):
    max_evidence_bytes: int = Field(default=1_048_576, ge=1, le=10_485_760)


class Scorecard(StrictModel):
    schema_version: Literal["proofstate.dev/scorecard/v1alpha1"]
    repository: RepositoryTarget
    settings: Settings = Field(default_factory=Settings)
    assertions: list[Assertion] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_graph(self) -> Scorecard:
        ids = [assertion.id for assertion in self.assertions]
        if len(ids) != len(set(ids)):
            raise ValueError("assertion ids must be unique")
        known = set(ids)
        for assertion in self.assertions:
            missing = set(assertion.depends_on) - known
            if missing:
                raise ValueError(
                    f"assertion {assertion.id!r} has unknown dependencies: {sorted(missing)!r}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {assertion.id: assertion for assertion in self.assertions}

        def visit(assertion_id: str) -> None:
            if assertion_id in visiting:
                raise ValueError("assertion dependency graph contains a cycle")
            if assertion_id in visited:
                return
            visiting.add(assertion_id)
            for dependency in by_id[assertion_id].depends_on:
                visit(dependency)
            visiting.remove(assertion_id)
            visited.add(assertion_id)

        for assertion_id in ids:
            visit(assertion_id)
        return self


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if RFC3339_TIMESTAMP.fullmatch(value) is None:
            raise ValueError("timestamp must be an RFC 3339 string")
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as error:
            raise ValueError("timestamp must be a valid RFC 3339 string") from error
    else:
        raise ValueError("timestamp must be an RFC 3339 string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include an offset")
    return parsed


class AttestationScope(StrictModel):
    repository: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    commit: GitObjectId
    assertions: list[Identifier] = Field(min_length=1, max_length=100)

    @field_validator("assertions")
    @classmethod
    def unique_assertions(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("scope assertions cannot contain duplicates")
        return value


class HumanAttestation(StrictModel):
    schema_version: Literal["proofstate.dev/attestation/v1alpha1"]
    identity: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    issued_at: datetime
    expires_at: datetime
    scope: AttestationScope
    statement: Annotated[str, StringConstraints(min_length=1, max_length=2_000)]

    _issued_at = field_validator("issued_at", mode="before")(parse_timestamp)
    _expires_at = field_validator("expires_at", mode="before")(parse_timestamp)

    @model_validator(mode="after")
    def validate_window(self) -> HumanAttestation:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        return self
