"""Create and evaluate a complete synthetic ProofState repository."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required executable is missing: {name}")
    return path


GIT = executable("git")


def git(repository: Path, *arguments: str) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Example Maintainer",
            "GIT_AUTHOR_EMAIL": "maintainer@example.invalid",
            "GIT_COMMITTER_NAME": "Example Maintainer",
            "GIT_COMMITTER_EMAIL": "maintainer@example.invalid",
            "LC_ALL": "C",
        }
    )
    process = subprocess.run(
        [GIT, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return process.stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_scorecard(commit: str) -> dict[str, Any]:
    return {
        "schema_version": "proofstate.dev/scorecard/v1alpha1",
        "repository": {
            "identity": "example.invalid/platform/widget",
            "commit": commit,
        },
        "assertions": [
            {
                "id": "release-test",
                "title": "Release test and result are represented",
                "severity": "critical",
                "failure_cap": "none",
                "depends_on": [],
                "evidence": {
                    "machine": [
                        {
                            "type": "test_symbol",
                            "framework": "pytest",
                            "path": "tests/test_release.py",
                            "symbol": "test_release_path",
                        },
                        {
                            "type": "artifact",
                            "path": "evidence/result.json",
                            "format": "json",
                            "checks": [
                                {"pointer": "/failed", "operator": "equals", "expected": 0},
                                {"pointer": "/passed", "operator": "gte", "expected": 1},
                            ],
                        },
                    ],
                    "attestations": [],
                },
            },
            {
                "id": "security-review",
                "title": "Security review is current and scoped",
                "severity": "high",
                "failure_cap": "merge",
                "depends_on": ["release-test"],
                "evidence": {
                    "machine": [],
                    "attestations": [
                        {
                            "type": "human_attestation",
                            "path": ".proofstate/attestations/security.json",
                        }
                    ],
                },
            },
        ],
    }


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="proofstate-example-") as directory:
        repository = Path(directory)
        git(repository, "init", "-b", "main")
        (repository / "tests").mkdir()
        (repository / "tests/test_release.py").write_text(
            "def test_release_path():\n    assert True\n",
            encoding="utf-8",
        )
        write_json(repository / "evidence/result.json", {"passed": 8, "failed": 0})
        git(repository, "add", "--", "tests/test_release.py")
        git(repository, "add", "--", "evidence/result.json")
        git(repository, "commit", "-m", "Add synthetic release evidence")
        evidence_commit = git(repository, "rev-parse", "HEAD")

        write_json(repository / ".proofstate/scorecard.yaml", build_scorecard(evidence_commit))
        write_json(
            repository / ".proofstate/attestations/security.json",
            {
                "schema_version": "proofstate.dev/attestation/v1alpha1",
                "identity": "security-reviewer@example.invalid",
                "issued_at": "2026-08-01T00:00:00Z",
                "expires_at": "2027-08-01T00:00:00Z",
                "scope": {
                    "repository": "example.invalid/platform/widget",
                    "commit": evidence_commit,
                    "assertions": ["security-review"],
                },
                "statement": "The synthetic release evidence was reviewed.",
            },
        )
        git(repository, "add", "--", ".proofstate/scorecard.yaml")
        git(repository, "add", "--", ".proofstate/attestations/security.json")
        git(repository, "commit", "-m", "Record synthetic readiness policy")

        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "proofstate",
                "check",
                ".proofstate/scorecard.yaml",
                "--repo",
                str(repository),
                "--at",
                "2026-08-24T00:00:00Z",
            ],
            check=True,
            text=True,
        )
        if process.returncode != 0:
            raise RuntimeError("example evaluation failed")


if __name__ == "__main__":
    run()
