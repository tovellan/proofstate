from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


def find_git() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is required for integration tests")
    return executable


GIT = find_git()


def git(repository: Path, *args: str) -> str:
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
    process: subprocess.CompletedProcess[str] = subprocess.run(
        [GIT, "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return process.stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(slots=True)
class RepositoryFixture:
    root: Path
    target_commit: str
    policy_commit: str
    scorecard: dict[str, Any]
    attestation: dict[str, Any]

    def commit_policy(self, scorecard: dict[str, Any] | None = None) -> str:
        if scorecard is not None:
            self.scorecard = scorecard
        write_json(self.root / ".proofstate/scorecard.yaml", self.scorecard)
        write_json(self.root / ".proofstate/attestations/review.json", self.attestation)
        git(self.root, "add", "--", ".proofstate/scorecard.yaml")
        git(self.root, "add", "--", ".proofstate/attestations/review.json")
        git(self.root, "commit", "-m", "Record readiness policy")
        self.policy_commit = git(self.root, "rev-parse", "HEAD")
        return self.policy_commit

    def copy_scorecard(self) -> dict[str, Any]:
        return deepcopy(self.scorecard)


@pytest.fixture
def repository_fixture(tmp_path: Path) -> RepositoryFixture:
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Example Maintainer")
    git(root, "config", "user.email", "maintainer@example.invalid")

    (root / "src").mkdir()
    (root / "src/widget.py").write_text("VALUE = 7\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/test_widget.py").write_text(
        "def test_widget():\n    assert True\n\n"
        "class TestWidget:\n    async def test_async(self):\n        assert True\n",
        encoding="utf-8",
    )
    write_json(
        root / "evidence/report.json",
        {"tests": {"passed": 12, "failed": 0}, "status": "ready", "labels": ["ci"]},
    )
    git(root, "add", "--", "src/widget.py")
    git(root, "add", "--", "tests/test_widget.py")
    git(root, "add", "--", "evidence/report.json")
    git(root, "commit", "-m", "Add synthetic application evidence")
    target_commit = git(root, "rev-parse", "HEAD")

    attestation: dict[str, Any] = {
        "schema_version": "proofstate.dev/attestation/v1alpha1",
        "identity": "security-reviewer@example.invalid",
        "issued_at": "2025-12-01T00:00:00Z",
        "expires_at": "2027-01-01T00:00:00Z",
        "scope": {
            "repository": "example.invalid/platform/widget",
            "commit": target_commit,
            "assertions": ["human-review"],
        },
        "statement": "The synthetic threat model was reviewed for the named commit.",
    }
    source_digest = hashlib.sha256((root / "src/widget.py").read_bytes()).hexdigest()
    scorecard: dict[str, Any] = {
        "schema_version": "proofstate.dev/scorecard/v1alpha1",
        "repository": {
            "identity": "example.invalid/platform/widget",
            "commit": target_commit,
        },
        "settings": {"max_evidence_bytes": 1048576},
        "assertions": [
            {
                "id": "source-present",
                "title": "Source is present",
                "severity": "high",
                "failure_cap": "none",
                "depends_on": [],
                "evidence": {
                    "machine": [{"type": "file", "path": "src/widget.py", "sha256": source_digest}],
                    "attestations": [],
                },
            },
            {
                "id": "named-test",
                "title": "A named test is present",
                "severity": "medium",
                "failure_cap": "advisory",
                "depends_on": ["source-present"],
                "evidence": {
                    "machine": [
                        {
                            "type": "test_symbol",
                            "path": "tests/test_widget.py",
                            "symbol": "TestWidget.test_async",
                            "framework": "pytest",
                        }
                    ],
                    "attestations": [],
                },
            },
            {
                "id": "structured-result",
                "title": "Structured test result is ready",
                "severity": "critical",
                "failure_cap": "merge",
                "depends_on": ["named-test"],
                "evidence": {
                    "machine": [
                        {
                            "type": "artifact",
                            "path": "evidence/report.json",
                            "format": "json",
                            "checks": [
                                {"pointer": "/status", "operator": "equals", "expected": "ready"},
                                {"pointer": "/tests/failed", "operator": "equals", "expected": 0},
                                {"pointer": "/tests/passed", "operator": "gte", "expected": 1},
                                {"pointer": "/labels", "operator": "contains", "expected": "ci"},
                                {"pointer": "/tests", "operator": "type", "expected": "object"},
                                {"pointer": "/status", "operator": "exists"},
                            ],
                        }
                    ],
                    "attestations": [],
                },
            },
            {
                "id": "human-review",
                "title": "Human security review is current",
                "severity": "high",
                "failure_cap": "merge",
                "depends_on": ["structured-result"],
                "evidence": {
                    "machine": [],
                    "attestations": [
                        {
                            "type": "human_attestation",
                            "path": ".proofstate/attestations/review.json",
                        }
                    ],
                },
            },
        ],
    }
    fixture = RepositoryFixture(root, target_commit, "", scorecard, attestation)
    fixture.commit_policy()
    return fixture
