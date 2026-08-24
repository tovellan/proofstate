"""Measure ProofState on reproducible assertion graphs, histories, and clone shapes."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from proofstate.errors import ProofStateError
from proofstate.evaluate import evaluate_scorecard

git_path = shutil.which("git")
if git_path is None:
    raise RuntimeError("git is required")
GIT: str = git_path

EVALUATED_AT = datetime(2026, 8, 24, tzinfo=UTC)


def _environment(index: int = 0, *, allow_lazy_fetch: bool = False) -> dict[str, str]:
    instant = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index)
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Example Maintainer",
            "GIT_AUTHOR_EMAIL": "maintainer@example.invalid",
            "GIT_COMMITTER_NAME": "Example Maintainer",
            "GIT_COMMITTER_EMAIL": "maintainer@example.invalid",
            "GIT_AUTHOR_DATE": instant.isoformat(),
            "GIT_COMMITTER_DATE": instant.isoformat(),
            "LC_ALL": "C",
        }
    )
    if allow_lazy_fetch:
        environment.pop("GIT_NO_LAZY_FETCH", None)
    else:
        environment["GIT_NO_LAZY_FETCH"] = "1"
    return environment


def _git(
    repository: Path,
    *arguments: str,
    check: bool = True,
    commit_index: int = 0,
    allow_lazy_fetch: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GIT, "-C", str(repository), *arguments],
        check=check,
        capture_output=True,
        text=True,
        env=_environment(commit_index, allow_lazy_fetch=allow_lazy_fetch),
        timeout=30,
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _scorecard(commit: str, assertions: int) -> dict[str, Any]:
    return {
        "schema_version": "proofstate.dev/scorecard/v1alpha1",
        "repository": {"identity": "example.invalid/benchmark", "commit": commit},
        "assertions": [
            {
                "id": f"source-{index:04d}",
                "title": f"Source assertion {index}",
                "severity": "high",
                "failure_cap": "none",
                "depends_on": [] if index == 0 else [f"source-{index - 1:04d}"],
                "evidence": {
                    "machine": [{"type": "file", "path": "evidence.txt"}],
                    "attestations": [],
                },
            }
            for index in range(assertions)
        ],
    }


def build_repository(
    root: Path,
    *,
    assertions: int,
    intermediate_history_commits: int,
    remove_evidence_at_tip: bool = False,
) -> tuple[str, str]:
    root.mkdir()
    _git(root, "init", "-b", "main")
    (root / "evidence.txt").write_text("bounded benchmark evidence\n", encoding="utf-8")
    _git(root, "add", "--", "evidence.txt")
    _git(root, "commit", "-m", "Add benchmark evidence", commit_index=0)
    evidence_commit = _git(root, "rev-parse", "HEAD").stdout.strip()
    for index in range(intermediate_history_commits):
        _git(
            root,
            "commit",
            "--allow-empty",
            "-m",
            f"Synthetic history {index:04d}",
            commit_index=index + 1,
        )
    if remove_evidence_at_tip:
        _git(root, "rm", "--", "evidence.txt")
        _git(root, "commit", "-m", "Remove evidence from tip", commit_index=20_000)
    _write_json(root / ".proofstate/scorecard.json", _scorecard(evidence_commit, assertions))
    _git(root, "add", "--", ".proofstate/scorecard.json")
    _git(root, "commit", "-m", "Record benchmark policy", commit_index=20_001)
    return evidence_commit, _git(root, "rev-parse", "HEAD").stdout.strip()


def _evaluate(repository: Path) -> bool:
    result = evaluate_scorecard(
        ".proofstate/scorecard.json",
        repository_path=repository,
        evaluated_at=EVALUATED_AT,
    )
    return result.passed


def benchmark_case(
    root: Path,
    *,
    assertions: int,
    intermediate_history_commits: int,
    repetitions: int,
) -> dict[str, Any]:
    evidence_commit, policy_commit = build_repository(
        root,
        assertions=assertions,
        intermediate_history_commits=intermediate_history_commits,
    )
    if not _evaluate(root):
        raise RuntimeError("benchmark fixture did not pass")
    durations: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        passed = _evaluate(root)
        durations.append(time.perf_counter() - started)
        if not passed:
            raise RuntimeError("benchmark evaluation did not pass")
    return {
        "assertions": assertions,
        "intermediate_history_commits": intermediate_history_commits,
        "repetitions": repetitions,
        "scorecard_bytes": (root / ".proofstate/scorecard.json").stat().st_size,
        "evidence_commit": evidence_commit,
        "policy_commit": policy_commit,
        "median_seconds": statistics.median(durations),
        "minimum_seconds": min(durations),
        "maximum_seconds": max(durations),
        "passed": True,
    }


def benchmark_clone_constraints(root: Path) -> dict[str, Any]:
    root.mkdir()
    source = root / "source"
    evidence_commit, policy_commit = build_repository(
        source,
        assertions=1,
        intermediate_history_commits=2,
        remove_evidence_at_tip=True,
    )
    remote = root / "remote.git"
    subprocess.run(
        [GIT, "clone", "--bare", str(source), str(remote)],
        check=True,
        capture_output=True,
        text=True,
        env=_environment(),
        timeout=30,
    )
    _git(remote, "config", "uploadpack.allowFilter", "true")
    _git(remote, "config", "uploadpack.allowAnySHA1InWant", "true")
    remote_url = remote.resolve().as_uri()
    clones = {
        "full": ["clone", remote_url, str(root / "full")],
        "shallow": ["clone", "--depth", "1", remote_url, str(root / "shallow")],
        "partial": [
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            remote_url,
            str(root / "partial"),
        ],
    }
    for arguments in clones.values():
        subprocess.run(
            [GIT, *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=_environment(),
            timeout=30,
        )
    _git(root / "partial", "checkout", "main", allow_lazy_fetch=True)

    behavior: dict[str, Any] = {
        "evidence_commit": evidence_commit,
        "policy_commit": policy_commit,
    }
    behavior["full"] = {"outcome": "pass" if _evaluate(root / "full") else "fail"}
    try:
        _evaluate(root / "shallow")
    except ProofStateError as error:
        behavior["shallow"] = {"outcome": "error", "code": error.code.value}
    else:
        behavior["shallow"] = {"outcome": "unexpected_evaluation"}
    partial = evaluate_scorecard(
        ".proofstate/scorecard.json",
        repository_path=root / "partial",
        evaluated_at=EVALUATED_AT,
    )
    behavior["partial"] = {
        "outcome": "pass" if partial.passed else "fail",
        "evidence_codes": sorted(
            {evidence.code for assertion in partial.assertions for evidence in assertion.evidence}
        ),
    }
    return behavior


def run_benchmarks(
    root: Path,
    *,
    assertion_counts: list[int],
    history_depths: list[int],
    repetitions: int,
) -> dict[str, Any]:
    cases = [
        benchmark_case(
            root / f"assertions-{count}",
            assertions=count,
            intermediate_history_commits=0,
            repetitions=repetitions,
        )
        for count in assertion_counts
    ]
    cases.extend(
        [
            benchmark_case(
                root / f"history-{depth}",
                assertions=1,
                intermediate_history_commits=depth,
                repetitions=repetitions,
            )
            for depth in history_depths
        ]
    )
    git_version = subprocess.run(
        [GIT, "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=_environment(),
        timeout=30,
    ).stdout.strip()
    return {
        "schema_version": "proofstate.dev/benchmark-result/v1alpha1",
        "inputs": {
            "assertion_counts": assertion_counts,
            "history_depths": history_depths,
            "repetitions": repetitions,
            "evaluated_at": EVALUATED_AT.isoformat(),
            "evidence_bytes": len(b"bounded benchmark evidence\n"),
            "dependency_shape": "linear-chain",
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "machine": platform.machine(),
            "git": git_version,
        },
        "cases": cases,
        "clone_constraints": benchmark_clone_constraints(root / "clones"),
    }


def _positive_csv(value: str) -> list[int]:
    values = [int(item) for item in value.split(",")]
    if not values or any(item < 1 for item in values):
        raise argparse.ArgumentTypeError("values must be positive comma-separated integers")
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assertions", type=_positive_csv, default=[100, 500, 1000])
    parser.add_argument("--histories", type=_positive_csv, default=[100, 1000])
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.repetitions < 1:
        parser.error("--repetitions must be positive")
    with tempfile.TemporaryDirectory(prefix="proofstate-benchmark-") as directory:
        result = run_benchmarks(
            Path(directory),
            assertion_counts=arguments.assertions,
            history_depths=arguments.histories,
            repetitions=arguments.repetitions,
        )
    output = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(output, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")
        print(arguments.output)


if __name__ == "__main__":
    main()
