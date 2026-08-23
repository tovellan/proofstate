"""Stable errors exposed by the library and command line interface."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "PS001_INVALID_ARGUMENT"
    NOT_A_GIT_REPOSITORY = "PS002_NOT_A_GIT_REPOSITORY"
    GIT_COMMAND_FAILED = "PS003_GIT_COMMAND_FAILED"
    SCORECARD_NOT_FOUND = "PS004_SCORECARD_NOT_FOUND"
    SCORECARD_TOO_LARGE = "PS005_SCORECARD_TOO_LARGE"
    INVALID_DOCUMENT = "PS006_INVALID_DOCUMENT"
    INVALID_SCORECARD = "PS007_INVALID_SCORECARD"
    UNRESOLVABLE_COMMIT = "PS008_UNRESOLVABLE_COMMIT"
    UNRELATED_COMMIT = "PS009_UNRELATED_COMMIT"
    UNSUPPORTED_OBJECT_FORMAT = "PS010_UNSUPPORTED_OBJECT_FORMAT"
    INVALID_TIME = "PS011_INVALID_TIME"
    CONFORMANCE_EXPORT_FAILED = "PS012_CONFORMANCE_EXPORT_FAILED"


class ProofStateError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def __str__(self) -> str:
        return self.message
