# ProofState

ProofState verifies repository readiness assertions against evidence stored in
specific Git trees. It prevents a scorecard from passing when its evidence is
missing, stale, mutable, unrelated, or outside a human attestation's scope.

Teams often track release readiness in editable spreadsheets or Markdown. Those
formats can describe a decision, but they do not prove that the named file, test,
artifact, or review exists for the commit being released. ProofState makes that
relationship executable and returns a machine-readable result.

## What it verifies

- Versioned YAML or JSON scorecards with strict unknown-field rejection.
- Full Git object IDs that resolve to an ancestor of the scorecard revision.
- Regular-file existence and optional SHA-256 digests in the pinned tree.
- Named pytest functions and class methods parsed from the pinned tree.
- Bounded JSON or YAML artifacts checked with JSON Pointer conditions.
- Human attestations with identity, issue time, expiration, repository, commit,
  and assertion scope.
- Assertion dependencies, severity labels, and failure caps for release gates.
- Fail-closed behavior for malformed, oversized, missing, or unreadable evidence.

ProofState reads Git objects. It does not trust an uncommitted working tree.

## Install

ProofState requires Python 3.11 or later and Git.

Install the released source with `uv`:

```sh
uv tool install git+https://github.com/tovellan/proofstate@v0.1.0
proofstate --version
```

For repository development:

```sh
git clone https://github.com/tovellan/proofstate.git
cd proofstate
uv sync --locked --all-groups
uv run proofstate --version
```

No package is published to PyPI in version 0.1.0.

## Run the complete example

The example creates a temporary synthetic repository, commits machine evidence,
adds a scoped attestation in a later commit, and asks for the release gate:

```sh
uv run python examples/basic/run.py
```

Expected first line:

```text
PASS required=release achieved=release
```

The script prints temporary commit IDs after that stable prefix.

## Check a repository

```sh
proofstate check .proofstate/scorecard.yaml \
  --repo . \
  --scorecard-ref HEAD \
  --require release
```

Use JSON in CI:

```sh
proofstate check .proofstate/scorecard.yaml --format json > proofstate-result.json
```

Exit status `0` means the requested gate is achieved. Status `1` means the
scorecard evaluated but did not achieve the gate. Status `2` means the input or
repository could not be evaluated.

## Scorecard shape

Machine evidence and human attestations are deliberately separate:

```yaml
schema_version: proofstate.dev/scorecard/v1alpha1
repository:
  identity: example.invalid/platform/widget
  commit: 0123456789abcdef0123456789abcdef01234567
assertions:
  - id: release-tests
    title: Release tests are represented by a named test and result artifact
    severity: critical
    failure_cap: merge
    depends_on: []
    evidence:
      machine:
        - type: test_symbol
          framework: pytest
          path: tests/test_release.py
          symbol: test_release_path
        - type: artifact
          path: evidence/test-result.json
          format: json
          checks:
            - pointer: /failed
              operator: equals
              expected: 0
      attestations: []
```

The placeholder commit above demonstrates syntax only. A real scorecard must use
the full object ID of a commit that exists in the repository and is an ancestor
of the scorecard revision.

## Gate model

Evaluation starts at `release`. Each failed or dependency-blocked assertion caps
the achieved level at its `failure_cap`:

| Failure cap | Highest achieved level after failure |
| --- | --- |
| `merge` | merge |
| `advisory` | advisory |
| `none` | none |

Severity is reported independently as `low`, `medium`, `high`, or `critical`.
It supports triage without silently changing gate policy.

## Security boundary

A human attestation is a scoped declaration, not a cryptographic identity proof.
ProofState protects its content from uncommitted mutation by loading it from the
scorecard Git tree, and checks its time and scope. Teams that need signer
authentication should require signed commits or add a signature-verification
step before ProofState. Version 0.1.0 does not fetch remote evidence, execute
tests, validate commit signatures, or establish that an attested identity maps
to a real person.

See the [threat model](docs/security/THREAT_MODEL.md),
[scorecard reference](docs/reference/SCORECARD.md), and
[architecture](docs/architecture/OVERVIEW.md) before adopting a release gate.

## Development

```sh
make lint
make test
make build
make example
make gate
```

`make gate` runs formatting, typing, tests, build verification, dependency audit,
clean-wheel installation, the executable example, tracked-text checks, and a
full-history secret scan when `gitleaks` is installed.

## Status

ProofState 0.1.0 supports local Git worktrees and SHA-1 or SHA-256 Git object
formats. The schema is `v1alpha1`: unknown fields fail validation, but compatible
additive evolution is not promised until a stable schema is released.

ProofState is licensed under Apache License 2.0.
