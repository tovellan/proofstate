"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from proofstate import __version__
from proofstate.conformance import ConformanceResult, export_conformance, run_conformance
from proofstate.errors import ErrorCode, ProofStateError
from proofstate.evaluate import Evaluation, evaluate_scorecard
from proofstate.models import GateLevel, HumanAttestation, Scorecard, parse_timestamp


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ProofStateError(ErrorCode.INVALID_ARGUMENT, message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="proofstate",
        description="Verify repository readiness evidence against pinned Git trees.",
    )
    parser.add_argument("--version", action="version", version=f"proofstate {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="evaluate a tracked scorecard")
    check.add_argument("scorecard", help="repository-relative scorecard path")
    check.add_argument("--repo", default=".", help="path inside the Git worktree")
    check.add_argument(
        "--scorecard-ref",
        default="HEAD",
        help="Git revision containing the scorecard and attestations",
    )
    check.add_argument(
        "--require",
        choices=[level.value for level in GateLevel],
        default=GateLevel.RELEASE.value,
        help="minimum gate required for exit status 0",
    )
    check.add_argument("--at", help="RFC 3339 evaluation time; defaults to the current time")
    check.add_argument("--format", choices=["text", "json"], default="text")

    schema = subparsers.add_parser("schema", help="print a versioned JSON Schema")
    schema.add_argument(
        "kind",
        nargs="?",
        choices=["scorecard", "attestation"],
        default="scorecard",
    )
    schema.add_argument("--format", choices=["json"], default="json")

    conformance = subparsers.add_parser(
        "conformance",
        help="verify the installed v1alpha1 conformance bundle",
    )
    conformance.add_argument("--format", choices=["text", "json"], default="text")
    conformance.add_argument(
        "--export",
        metavar="DIRECTORY",
        help="write the verified corpus to a new directory",
    )
    return parser


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _console_text(value: str, *, stream: object = sys.stdout) -> str:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    return value.encode(encoding, errors="backslashreplace").decode(encoding)


def _print_text(result: Evaluation) -> None:
    verdict = "PASS" if result.passed else "FAIL"
    print(
        f"{verdict} required={result.required_gate.value} "
        f"achieved={result.achieved_gate.value} evidence={result.evidence_commit}"
    )
    for assertion in result.assertions:
        print(
            _console_text(
                f"[{assertion.status.upper():7}] {assertion.assertion_id} "
                f"severity={assertion.severity} cap={assertion.failure_cap}"
            )
        )
        for evidence in assertion.evidence:
            print(f"  {evidence.code.value} {evidence.message}")


def _print_conformance_text(result: ConformanceResult) -> None:
    verdict = "PASS" if result.passed else "FAIL"
    print(f"{verdict} conformance={result.schema_version} cases={len(result.cases)}")
    for case in result.cases:
        print(f"[{('PASS' if case.passed else 'FAIL'):4}] {case.case_id} observed={case.observed}")


def _fail(error: ProofStateError, output_format: str) -> NoReturn:
    payload: dict[str, object] = {
        "schema_version": "proofstate.dev/error/v1alpha1",
        "code": error.code.value,
        "message": error.message,
    }
    if error.details:
        payload["details"] = error.details
    if output_format == "json":
        _print_json(payload)
    else:
        print(
            _console_text(f"{error.code.value}: {error.message}", stream=sys.stderr),
            file=sys.stderr,
        )
    raise SystemExit(2)


def _requested_format(arguments: list[str]) -> str:
    output_format = "text"
    for index, argument in enumerate(arguments):
        if argument.startswith("--format="):
            output_format = argument.partition("=")[2]
        elif argument == "--format" and index + 1 < len(arguments):
            output_format = arguments[index + 1]
    return "json" if output_format == "json" else "text"


def _parse_time(value: str | None, output_format: str) -> datetime | None:
    if value is None:
        return None
    try:
        return parse_timestamp(value)
    except (ValueError, TypeError) as error:
        _fail(
            ProofStateError(
                code=ErrorCode.INVALID_TIME,
                message=str(error),
            ),
            output_format,
        )


def main(argv: list[str] | None = None) -> None:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    requested_format = _requested_format(raw_arguments)
    try:
        arguments = _parser().parse_args(raw_arguments)
    except ProofStateError as error:
        _fail(error, requested_format)
    if arguments.command == "schema":
        model = Scorecard if arguments.kind == "scorecard" else HumanAttestation
        _print_json(model.model_json_schema())
        return
    if arguments.command == "conformance":
        if arguments.export:
            try:
                conformance_result = export_conformance(Path(arguments.export))
            except (OSError, ValueError) as error:
                _fail(
                    ProofStateError(
                        ErrorCode.CONFORMANCE_EXPORT_FAILED,
                        str(error),
                    ),
                    arguments.format,
                )
        else:
            conformance_result = run_conformance()
        if arguments.format == "json":
            _print_json(conformance_result.to_dict())
        else:
            _print_conformance_text(conformance_result)
        if not conformance_result.passed:
            raise SystemExit(1)
        return

    output_format: str = arguments.format
    try:
        result = evaluate_scorecard(
            arguments.scorecard,
            repository_path=Path(arguments.repo),
            scorecard_ref=arguments.scorecard_ref,
            required_gate=GateLevel(arguments.require),
            evaluated_at=_parse_time(arguments.at, output_format),
        )
    except ProofStateError as error:
        _fail(error, output_format)
    if output_format == "json":
        _print_json(result.to_dict())
    else:
        _print_text(result)
    raise SystemExit(0 if result.passed else 1)
