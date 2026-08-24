# Performance and clone constraints

ProofState publishes a reproducible scale harness instead of claiming a general
throughput guarantee. Run the default matrix from a source checkout:

```sh
uv run python scripts/benchmark_scale.py
```

Use `--output PATH` to retain the JSON result. The harness records its exact
assertion counts, history depths, dependency shape, evidence size, repetitions,
evaluation time, Git version, Python version, operating system, and machine
architecture. Repository construction is excluded from the recorded durations.
Each case receives one unrecorded warm-up evaluation followed by five measured
evaluations by default.

## Recorded result

The checked-in result is
[`docs/benchmarks/2026-08-24-macos-arm64.json`](benchmarks/2026-08-24-macos-arm64.json).
It was measured with CPython 3.11.14 and Git 2.52.0 on Darwin arm64. These are
observations from that machine, not latency promises for another host.

| Assertions | Intermediate commits | Scorecard bytes | Median seconds |
| ---: | ---: | ---: | ---: |
| 100 | 0 | 36,168 | 0.104721 |
| 500 | 0 | 180,568 | 0.320461 |
| 1,000 | 0 | 361,068 | 0.588676 |
| 1 | 100 | 537 | 0.058316 |
| 1 | 1,000 | 537 | 0.077045 |

Every recorded evaluation passed. The assertion cases form a linear dependency
chain and reuse one 27-byte file evidence object. They measure graph traversal,
result construction, and immutable Git lookup reuse; they do not represent
large unique evidence files or a general workload distribution.

## Clone policy

ProofState never fetches missing Git objects during evaluation. Every Git
subprocess sets `GIT_NO_LAZY_FETCH=1`, so repository preparation is an explicit
caller responsibility.

The harness records these outcomes from the same deterministic repository:

- A full clone passes.
- A depth-one shallow clone that lacks the scorecard's pinned evidence commit
  stops with `PS008_UNRESOLVABLE_COMMIT`.
- A blob-filtered partial clone that has the commits and scorecard but lacks the
  historical evidence blob evaluates fail closed with `PSE900_INTERNAL_ERROR`.

Clone labels alone do not decide the result. A shallow or partial clone can pass
when every required commit, tree, and blob is already materialized locally. A
full clone can still fail if its object database is corrupt. Operators may fetch
or otherwise materialize required objects before running ProofState, but the
evaluator itself performs no network recovery and does not treat object absence
as successful evidence.

## Regression policy

The test suite checks the harness result shape and the exact full, shallow, and
partial-clone classifications without asserting a machine-dependent duration.
The separate bounded performance regression test retains its deliberately loose
ten-second ceiling for 100 assertions. A performance claim belongs in public
documentation only when a checked-in harness result identifies the inputs and
environment that produced it.
