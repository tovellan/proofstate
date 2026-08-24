from __future__ import annotations

from pathlib import Path

from scripts.benchmark_scale import benchmark_clone_constraints, run_benchmarks


def test_scale_harness_records_reproducible_inputs(tmp_path: Path) -> None:
    result = run_benchmarks(
        tmp_path,
        assertion_counts=[3],
        history_depths=[2],
        repetitions=1,
    )

    assert result["schema_version"] == "proofstate.dev/benchmark-result/v1alpha1"
    assert result["inputs"] == {
        "assertion_counts": [3],
        "history_depths": [2],
        "repetitions": 1,
        "evaluated_at": "2026-08-24T00:00:00+00:00",
        "evidence_bytes": 27,
        "dependency_shape": "linear-chain",
    }
    cases = result["cases"]
    assert isinstance(cases, list)
    assert [(case["assertions"], case["intermediate_history_commits"]) for case in cases] == [
        (3, 0),
        (1, 2),
    ]
    assert all(case["passed"] is True for case in cases)
    assert result["clone_constraints"]["full"] == {"outcome": "pass"}
    assert result["clone_constraints"]["shallow"] == {
        "outcome": "error",
        "code": "PS008_UNRESOLVABLE_COMMIT",
    }
    assert result["clone_constraints"]["partial"] == {
        "outcome": "fail",
        "evidence_codes": ["PSE900_INTERNAL_ERROR"],
    }


def test_clone_constraint_harness_is_repeatable(tmp_path: Path) -> None:
    first = benchmark_clone_constraints(tmp_path / "first")
    second = benchmark_clone_constraints(tmp_path / "second")

    for result in (first, second):
        assert result["full"] == {"outcome": "pass"}
        assert result["shallow"]["code"] == "PS008_UNRESOLVABLE_COMMIT"
        assert result["partial"]["evidence_codes"] == ["PSE900_INTERNAL_ERROR"]
