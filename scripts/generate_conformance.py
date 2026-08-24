"""Generate the portable v1alpha1 document conformance bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1] / "src" / "proofstate" / "fixtures" / "v1alpha1"
MANIFEST_SCHEMA = "proofstate.dev/conformance-manifest/v1alpha2"
RESULT_SCHEMA = "proofstate.dev/conformance-result/v1alpha1"
DocumentKind = Literal["scorecard", "attestation"]
Expected = Literal["valid", "invalid_document", "invalid_scorecard", "invalid_attestation"]


@dataclass(frozen=True, slots=True)
class Fixture:
    case_id: str
    document_kind: DocumentKind
    expected: Expected
    content: bytes

    @property
    def path(self) -> str:
        suffix = ".yaml" if self.content.startswith(b"schema_version:") else ".json"
        return f"{self.case_id}{suffix}"


def _json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _scorecard() -> dict[str, Any]:
    return {
        "schema_version": "proofstate.dev/scorecard/v1alpha1",
        "repository": {
            "identity": "example.invalid/widget",
            "commit": "0" * 40,
        },
        "assertions": [
            {
                "id": "source-present",
                "title": "Source is present",
                "severity": "high",
                "evidence": {
                    "machine": [{"type": "file", "path": "README.md"}],
                    "attestations": [],
                },
            }
        ],
    }


def _attestation() -> dict[str, Any]:
    return {
        "schema_version": "proofstate.dev/attestation/v1alpha1",
        "identity": "release-reviewer",
        "issued_at": "2026-08-24T00:00:00Z",
        "expires_at": "2026-08-25T00:00:00Z",
        "scope": {
            "repository": "example.invalid/widget",
            "commit": "0" * 40,
            "assertions": ["source-present"],
        },
        "statement": "The scoped assertion was reviewed.",
    }


def _mutated(value: dict[str, Any], mutation: Any) -> dict[str, Any]:
    changed = deepcopy(value)
    mutation(changed)
    return changed


def _complete_scorecard() -> dict[str, Any]:
    return {
        "schema_version": "proofstate.dev/scorecard/v1alpha1",
        "repository": {
            "identity": "example.invalid/widget",
            "commit": "a" * 64,
        },
        "settings": {"max_evidence_bytes": 10_485_760},
        "assertions": [
            {
                "id": "all-machine-evidence",
                "title": "Every machine evidence shape is represented",
                "severity": "critical",
                "failure_cap": "merge",
                "depends_on": [],
                "evidence": {
                    "machine": [
                        {"type": "file", "path": "README.md", "sha256": "b" * 64},
                        {
                            "type": "test_symbol",
                            "path": "tests/test_release.py",
                            "symbol": "TestRelease.test_candidate",
                            "framework": "pytest",
                        },
                        {
                            "type": "artifact",
                            "path": "evidence/result.json",
                            "format": "json",
                            "sha256": "c" * 64,
                            "checks": [
                                {"pointer": "/ready", "operator": "exists"},
                                {"pointer": "/ready", "operator": "equals", "expected": True},
                                {
                                    "pointer": "/status",
                                    "operator": "not_equals",
                                    "expected": "failed",
                                },
                                {
                                    "pointer": "/labels",
                                    "operator": "contains",
                                    "expected": "release",
                                },
                                {"pointer": "/passed", "operator": "gte", "expected": 1},
                                {"pointer": "/failed", "operator": "lte", "expected": 0.0},
                                {
                                    "pointer": "/summary",
                                    "operator": "type",
                                    "expected": "object",
                                },
                            ],
                        },
                    ],
                    "attestations": [],
                },
            },
            {
                "id": "human-review",
                "title": "Human review is represented",
                "severity": "medium",
                "failure_cap": "advisory",
                "depends_on": ["all-machine-evidence"],
                "evidence": {
                    "machine": [],
                    "attestations": [
                        {
                            "type": "human_attestation",
                            "path": ".proofstate/attestations/review.json",
                            "sha256": "d" * 64,
                        }
                    ],
                },
            },
        ],
    }


def _json_fixture(
    case_id: str,
    document_kind: DocumentKind,
    expected: Expected,
    value: object,
) -> Fixture:
    return Fixture(case_id, document_kind, expected, _json(value))


def _cases() -> list[Fixture]:
    scorecard = _scorecard()
    attestation = _attestation()
    invalid_scorecard: Expected = "invalid_scorecard"
    invalid_attestation: Expected = "invalid_attestation"
    cases = [
        _json_fixture("scorecard-valid-minimal", "scorecard", "valid", scorecard),
        _json_fixture("scorecard-valid-complete", "scorecard", "valid", _complete_scorecard()),
        _json_fixture(
            "scorecard-unknown-field",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value.update({"unexpected": True})),
        ),
        _json_fixture(
            "scorecard-invalid-schema-version",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value.update({"schema_version": "v1"})),
        ),
        _json_fixture(
            "scorecard-empty-repository-identity",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value["repository"].update({"identity": ""})),
        ),
        _json_fixture(
            "scorecard-long-repository-identity",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value["repository"].update({"identity": "r" * 241})),
        ),
        _json_fixture(
            "scorecard-invalid-commit",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value["repository"].update({"commit": "0" * 39})),
        ),
        _json_fixture(
            "scorecard-settings-below-minimum",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard, lambda value: value.update({"settings": {"max_evidence_bytes": 0}})
            ),
        ),
        _json_fixture(
            "scorecard-settings-above-maximum",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value.update({"settings": {"max_evidence_bytes": 10_485_761}}),
            ),
        ),
        _json_fixture(
            "scorecard-empty-assertions",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value.update({"assertions": []})),
        ),
        _json_fixture(
            "scorecard-too-many-assertions",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value.update(
                    {
                        "assertions": [
                            {
                                **deepcopy(scorecard["assertions"][0]),
                                "id": f"assertion-{index:04d}",
                            }
                            for index in range(1001)
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-assertion-id",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value["assertions"][0].update({"id": "Upper"})),
        ),
        _json_fixture(
            "scorecard-long-assertion-id",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value["assertions"][0].update({"id": "a" * 65})),
        ),
        _json_fixture(
            "scorecard-empty-title",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value["assertions"][0].update({"title": ""})),
        ),
        _json_fixture(
            "scorecard-long-title",
            "scorecard",
            invalid_scorecard,
            _mutated(scorecard, lambda value: value["assertions"][0].update({"title": "t" * 161})),
        ),
        _json_fixture(
            "scorecard-invalid-severity",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard, lambda value: value["assertions"][0].update({"severity": "urgent"})
            ),
        ),
        _json_fixture(
            "scorecard-invalid-failure-cap",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard, lambda value: value["assertions"][0].update({"failure_cap": "release"})
            ),
        ),
        _json_fixture(
            "scorecard-self-dependency",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0].update({"depends_on": ["source-present"]}),
            ),
        ),
        _json_fixture(
            "scorecard-duplicate-dependency",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value.update(
                    {
                        "assertions": [
                            deepcopy(scorecard["assertions"][0]),
                            {
                                **deepcopy(scorecard["assertions"][0]),
                                "id": "dependent",
                                "depends_on": ["source-present", "source-present"],
                            },
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-unknown-dependency",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard, lambda value: value["assertions"][0].update({"depends_on": ["missing"]})
            ),
        ),
        _json_fixture(
            "scorecard-duplicate-id",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value.update(
                    {"assertions": [deepcopy(scorecard["assertions"][0])] * 2}
                ),
            ),
        ),
        _json_fixture(
            "scorecard-dependency-cycle",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value.update(
                    {
                        "assertions": [
                            {
                                **deepcopy(scorecard["assertions"][0]),
                                "id": "first",
                                "depends_on": ["second"],
                            },
                            {
                                **deepcopy(scorecard["assertions"][0]),
                                "id": "second",
                                "depends_on": ["first"],
                            },
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-empty-evidence",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0].update(
                    {"evidence": {"machine": [], "attestations": []}}
                ),
            ),
        ),
        _json_fixture(
            "scorecard-too-many-machine-evidence",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {"machine": [{"type": "file", "path": "README.md"}] * 101}
                ),
            ),
        ),
        _json_fixture(
            "scorecard-too-many-attestation-references",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [],
                        "attestations": [{"type": "human_attestation", "path": "review.json"}]
                        * 101,
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-evidence-type",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {"machine": [{"type": "command", "path": "README.md"}]}
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-path",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"]["machine"][0].update(
                    {"path": "../README.md"}
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-sha256",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"]["machine"][0].update(
                    {"sha256": "g" * 64}
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-test-path",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "test_symbol",
                                "path": "tests/test_release.txt",
                                "symbol": "test_release",
                                "framework": "pytest",
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-test-symbol",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "test_symbol",
                                "path": "tests/test_release.py",
                                "symbol": "helper",
                                "framework": "pytest",
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-test-framework",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "test_symbol",
                                "path": "tests/test_release.py",
                                "symbol": "test_release",
                                "framework": "unittest",
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-artifact-format",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "artifact",
                                "path": "result.toml",
                                "format": "toml",
                                "checks": [{"pointer": "", "operator": "exists"}],
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-empty-artifact-checks",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "artifact",
                                "path": "result.json",
                                "format": "json",
                                "checks": [],
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-too-many-artifact-checks",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "artifact",
                                "path": "result.json",
                                "format": "json",
                                "checks": [{"pointer": "", "operator": "exists"}] * 101,
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-pointer",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "artifact",
                                "path": "result.json",
                                "format": "json",
                                "checks": [{"pointer": "/bad~2token", "operator": "exists"}],
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-exists-with-expected",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "artifact",
                                "path": "result.json",
                                "format": "json",
                                "checks": [{"pointer": "", "operator": "exists", "expected": None}],
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-missing-expected",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "artifact",
                                "path": "result.json",
                                "format": "json",
                                "checks": [{"pointer": "", "operator": "equals"}],
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-numeric-expected",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "artifact",
                                "path": "result.json",
                                "format": "json",
                                "checks": [
                                    {"pointer": "/count", "operator": "gte", "expected": True}
                                ],
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture(
            "scorecard-invalid-type-expected",
            "scorecard",
            invalid_scorecard,
            _mutated(
                scorecard,
                lambda value: value["assertions"][0]["evidence"].update(
                    {
                        "machine": [
                            {
                                "type": "artifact",
                                "path": "result.json",
                                "format": "json",
                                "checks": [
                                    {"pointer": "", "operator": "type", "expected": "integer"}
                                ],
                            }
                        ]
                    }
                ),
            ),
        ),
        _json_fixture("attestation-valid", "attestation", "valid", attestation),
        _json_fixture(
            "attestation-unknown-field",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value.update({"approved": True})),
        ),
        _json_fixture(
            "attestation-invalid-schema-version",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value.update({"schema_version": "v1"})),
        ),
        _json_fixture(
            "attestation-empty-identity",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value.update({"identity": ""})),
        ),
        _json_fixture(
            "attestation-long-identity",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value.update({"identity": "i" * 241})),
        ),
        _json_fixture(
            "attestation-invalid-timestamp",
            "attestation",
            invalid_attestation,
            _mutated(
                attestation, lambda value: value.update({"issued_at": "2026-08-24 00:00:00Z"})
            ),
        ),
        _json_fixture(
            "attestation-offsetless-timestamp",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value.update({"issued_at": "2026-08-24T00:00:00"})),
        ),
        _json_fixture(
            "attestation-invalid-window",
            "attestation",
            invalid_attestation,
            _mutated(
                attestation,
                lambda value: value.update(
                    {
                        "issued_at": "2026-08-25T00:00:00Z",
                        "expires_at": "2026-08-24T00:00:00Z",
                    }
                ),
            ),
        ),
        _json_fixture(
            "attestation-empty-repository",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value["scope"].update({"repository": ""})),
        ),
        _json_fixture(
            "attestation-long-repository",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value["scope"].update({"repository": "r" * 241})),
        ),
        _json_fixture(
            "attestation-invalid-commit",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value["scope"].update({"commit": "0" * 39})),
        ),
        _json_fixture(
            "attestation-empty-scope",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value["scope"].update({"assertions": []})),
        ),
        _json_fixture(
            "attestation-too-many-scope-assertions",
            "attestation",
            invalid_attestation,
            _mutated(
                attestation,
                lambda value: value["scope"].update(
                    {"assertions": [f"assertion-{index:03d}" for index in range(101)]}
                ),
            ),
        ),
        _json_fixture(
            "attestation-invalid-scope-id",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value["scope"].update({"assertions": ["Upper"]})),
        ),
        _json_fixture(
            "attestation-duplicate-scope",
            "attestation",
            invalid_attestation,
            _mutated(
                attestation,
                lambda value: value["scope"].update(
                    {"assertions": ["source-present", "source-present"]}
                ),
            ),
        ),
        _json_fixture(
            "attestation-empty-statement",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value.update({"statement": ""})),
        ),
        _json_fixture(
            "attestation-long-statement",
            "attestation",
            invalid_attestation,
            _mutated(attestation, lambda value: value.update({"statement": "s" * 2001})),
        ),
    ]
    duplicate_key = b"""{
  "schema_version": "proofstate.dev/scorecard/v1alpha1",
  "schema_version": "proofstate.dev/scorecard/v1alpha1",
  "repository": {
    "identity": "example.invalid/widget",
    "commit": "0000000000000000000000000000000000000000"
  },
  "assertions": []
}
"""
    cases.append(Fixture("scorecard-duplicate-key", "scorecard", "invalid_document", duplicate_key))
    yaml_cases: list[tuple[str, Expected, str]] = [
        (
            "scorecard-yaml-core-scalars",
            "valid",
            """schema_version: proofstate.dev/scorecard/v1alpha1
repository:
  identity: yes
  commit: "0000000000000000000000000000000000000000"
settings:
  max_evidence_bytes: 08
assertions:
  - id: core-scalars
    title: YAML 1.2 Core numeric scalars are accepted
    severity: high
    evidence:
      machine:
        - type: artifact
          path: evidence/result.yaml
          format: yaml
          checks:
            - pointer: /exponent
              operator: gte
              expected: 1e3
            - pointer: /leading-decimal
              operator: gte
              expected: .5
            - pointer: /octal
              operator: gte
              expected: 0o7
            - pointer: /hexadecimal
              operator: gte
              expected: 0x3A
""",
        ),
        (
            "scorecard-yaml-legacy-binary",
            invalid_scorecard,
            """schema_version: proofstate.dev/scorecard/v1alpha1
repository:
  identity: example.invalid/widget
  commit: "0000000000000000000000000000000000000000"
assertions:
  - id: legacy-binary
    title: Legacy binary syntax remains text
    severity: high
    evidence:
      machine:
        - type: artifact
          path: evidence/result.yaml
          format: yaml
          checks:
            - pointer: /value
              operator: gte
              expected: 0b10
""",
        ),
        (
            "scorecard-yaml-legacy-separator",
            invalid_scorecard,
            """schema_version: proofstate.dev/scorecard/v1alpha1
repository:
  identity: example.invalid/widget
  commit: "0000000000000000000000000000000000000000"
assertions:
  - id: legacy-separator
    title: Legacy numeric separators remain text
    severity: high
    evidence:
      machine:
        - type: artifact
          path: evidence/result.yaml
          format: yaml
          checks:
            - pointer: /value
              operator: gte
              expected: 1_000
""",
        ),
        (
            "scorecard-yaml-legacy-sexagesimal",
            invalid_scorecard,
            """schema_version: proofstate.dev/scorecard/v1alpha1
repository:
  identity: example.invalid/widget
  commit: "0000000000000000000000000000000000000000"
assertions:
  - id: legacy-sexagesimal
    title: Legacy sexagesimal syntax remains text
    severity: high
    evidence:
      machine:
        - type: artifact
          path: evidence/result.yaml
          format: yaml
          checks:
            - pointer: /value
              operator: gte
              expected: 1:20
""",
        ),
        (
            "scorecard-yaml-merge-key",
            "invalid_document",
            """schema_version: proofstate.dev/scorecard/v1alpha1
repository:
  identity: example.invalid/widget
  commit: "0000000000000000000000000000000000000000"
<<:
  assertions: []
assertions:
  - id: merge-key
    title: Plain merge keys are rejected
    severity: high
    evidence:
      machine:
        - type: file
          path: README.md
""",
        ),
        (
            "scorecard-yaml-nonfinite",
            "invalid_document",
            """schema_version: proofstate.dev/scorecard/v1alpha1
repository:
  identity: example.invalid/widget
  commit: "0000000000000000000000000000000000000000"
assertions:
  - id: nonfinite
    title: Non-finite values are rejected
    severity: high
    evidence:
      machine:
        - type: artifact
          path: evidence/result.yaml
          format: yaml
          checks:
            - pointer: /value
              operator: equals
              expected: .inf
""",
        ),
        (
            "scorecard-yaml-nonstring-key",
            "invalid_document",
            """schema_version: proofstate.dev/scorecard/v1alpha1
repository:
  identity: example.invalid/widget
  commit: "0000000000000000000000000000000000000000"
assertions:
  - id: nonstring-key
    title: Non-string mapping keys are rejected
    severity: high
    evidence:
      machine:
        - type: artifact
          path: evidence/result.yaml
          format: yaml
          checks:
            - pointer: /value
              operator: equals
              expected:
                true: rejected
""",
        ),
    ]
    cases.extend(
        Fixture(case_id, "scorecard", expected, content.encode())
        for case_id, expected, content in yaml_cases
    )
    return cases


def render_bundle() -> dict[str, bytes]:
    cases = _cases()
    case_records = [
        {
            "id": case.case_id,
            "document_kind": case.document_kind,
            "path": case.path,
            "sha256": hashlib.sha256(case.content).hexdigest(),
            "expected": case.expected,
        }
        for case in cases
    ]
    expected_results = {
        "schema_version": RESULT_SCHEMA,
        "passed": True,
        "fixture_schema_version": MANIFEST_SCHEMA,
        "cases": [
            {
                "id": case.case_id,
                "expected": case.expected,
                "observed": case.expected,
                "passed": True,
            }
            for case in cases
        ],
    }
    expected_content = _json(expected_results)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "expected_results": {
            "path": "expected-results.json",
            "sha256": hashlib.sha256(expected_content).hexdigest(),
        },
        "cases": case_records,
    }
    rendered = {case.path: case.content for case in cases}
    rendered["expected-results.json"] = expected_content
    rendered["manifest.json"] = _json(manifest)
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    rendered = render_bundle()
    existing = {path.name for path in ROOT.iterdir() if path.suffix in {".json", ".yaml"}}
    stale = existing - set(rendered)
    failures = [f"unexpected fixture: {name}" for name in sorted(stale)]
    for name, content in sorted(rendered.items()):
        path = ROOT / name
        if arguments.check:
            try:
                current = path.read_bytes()
            except OSError:
                failures.append(f"missing fixture: {name}")
            else:
                if current != content:
                    failures.append(f"fixture differs from generator: {name}")
        else:
            path.write_bytes(content)
    if failures:
        for failure in failures:
            print(failure)
        raise SystemExit(1)
    print(f"conformance bundle agrees with generator: {len(rendered) - 2} cases")


if __name__ == "__main__":
    main()
