# Basic example

`run.py` creates a temporary Git repository containing synthetic test and
artifact evidence. It commits a scorecard and scoped human attestation in a
second commit, then verifies the release gate.

Run it from the project root:

```sh
uv run python examples/basic/run.py
```

The temporary repository is removed after the evaluation.
