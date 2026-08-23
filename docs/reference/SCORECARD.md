# Scorecard reference

## Schema versions

- Scorecard: `proofstate.dev/scorecard/v1alpha1`
- Human attestation: `proofstate.dev/attestation/v1alpha1`
- Result: `proofstate.dev/result/v1alpha1`
- Error: `proofstate.dev/error/v1alpha1`

Print the scorecard JSON Schema with `proofstate schema`.

## Repository target

`repository.identity` is the stable identifier chosen by the repository owner.
Attestations must match it exactly. A URL is conventional but not required.

`repository.commit` is a lowercase, full SHA-1 or SHA-256 Git object ID. It must
resolve to a commit and be an ancestor of the scorecard revision.

## Assertions

Every assertion has:

- `id`: a unique lowercase identifier.
- `title`: a concise description.
- `severity`: `low`, `medium`, `high`, or `critical`.
- `failure_cap`: `none`, `advisory`, or `merge`.
- `depends_on`: zero or more assertion IDs.
- `evidence.machine`: machine-verifiable evidence.
- `evidence.attestations`: human attestation references.

At least one evidence item is required. Every evidence item must pass. A cycle,
unknown dependency, duplicate ID, duplicate key, or unknown field invalidates the
scorecard.

## File evidence

```yaml
type: file
path: pyproject.toml
sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

The path must identify a regular file in the evidence tree. Symlinks and tree
objects do not pass. `sha256` is optional because the Git tree already binds the
blob, but it can document a digest expected by another system.

## Pytest symbol evidence

```yaml
type: test_symbol
framework: pytest
path: tests/test_release.py
symbol: TestRelease.test_candidate
```

ProofState parses Python source and finds the dotted function or class-method
name. A symbol must be a top-level `test_*` function or a `Test*` class's
`test_*` method. ProofState does not import the module, collect pytest
parameters, or execute the test. Symbol existence proves that a named test is
represented in the pinned tree, not that the test passed. Pair it with a
structured result artifact when a passing execution is required.

## Structured artifact evidence

```yaml
type: artifact
path: evidence/result.json
format: json
checks:
  - pointer: /summary/failed
    operator: equals
    expected: 0
```

Pointers follow RFC 6901. Supported operators are:

| Operator | Behavior |
| --- | --- |
| `exists` | Pointer resolves. No `expected` field is allowed. |
| `equals` | Value and JSON type equal `expected`. |
| `not_equals` | Value or JSON type differs from `expected`. |
| `contains` | String/list contains a value, or object contains a key. |
| `gte` | Numeric value is at least `expected`; booleans are rejected. |
| `lte` | Numeric value is at most `expected`; booleans are rejected. |
| `type` | Value has JSON type `null`, `boolean`, `number`, `string`, `array`, or `object`. |

Artifacts can be JSON or the restricted YAML subset described in the
architecture document.

## Human attestation

Scorecard reference:

```yaml
type: human_attestation
path: .proofstate/attestations/security-review.json
```

Attestation file:

```yaml
schema_version: proofstate.dev/attestation/v1alpha1
identity: security-reviewer@example.invalid
issued_at: 2026-08-01T09:00:00Z
expires_at: 2026-09-01T09:00:00Z
scope:
  repository: example.invalid/platform/widget
  commit: 0123456789abcdef0123456789abcdef01234567
  assertions:
    - security-review
statement: The named commit was reviewed against the repository threat model.
```

An attestation passes only within its time window and exact repository, commit,
and assertion scope. `expires_at` is exclusive. The identity is a declaration;
see the threat model for its trust boundary.
