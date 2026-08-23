# CLI reference

## `proofstate check`

```text
proofstate check SCORECARD [--repo PATH] [--scorecard-ref REF]
                           [--require LEVEL] [--at TIME]
                           [--format text|json]
```

`SCORECARD` is repository-relative and loaded from `--scorecard-ref`, not the
working tree. `--require` defaults to `release`. `--at` accepts an RFC 3339 time
with an offset. JSON output is intended for CI and other tools.

Exit status:

- `0`: requested gate achieved.
- `1`: evaluation completed but requested gate was not achieved.
- `2`: invalid input, invalid scorecard, or repository-level evaluation error.

## `proofstate schema [scorecard|attestation]`

Prints a versioned document JSON Schema to standard output. The default remains
`scorecard` for compatibility.

## `proofstate conformance`

Verifies the installed, digest-pinned `v1alpha1` conformance fixtures. Use
`--format json` for a versioned result with each expected and observed case.
Use `--export DIRECTORY` to write the exact verified corpus into a new directory.
An existing destination fails with exit status `2` and is never modified.

## `proofstate --version`

Prints the installed version.

## Python API

```python
from datetime import UTC, datetime

from proofstate import evaluate_scorecard

result = evaluate_scorecard(
    ".proofstate/scorecard.yaml",
    repository_path=".",
    scorecard_ref="HEAD",
    evaluated_at=datetime.now(UTC),
)
if not result.passed:
    raise SystemExit(1)
```

`Evaluation.to_dict()` returns the same result structure used by JSON CLI output.
`ProofStateError` carries a stable `ErrorCode`, message, and optional structured
details for input errors.
