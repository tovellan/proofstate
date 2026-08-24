# Conformance bundle

ProofState installs a portable `v1alpha1` fixture corpus inside the wheel.
It gives independent parsers a shared set of valid and fail-closed documents.
The current `conformance-manifest/v1alpha2` bundle carries 64 cases for the
`scorecard/v1alpha1` and `attestation/v1alpha1` schemas. Seven YAML cases distinguish
Core scalar resolution from legacy YAML 1.1 spellings and cover merge-key,
non-finite, and non-string-key rejection.

Run the bundled verifier:

```sh
proofstate conformance --format json
```

Export the portable inputs from an installed wheel:

```sh
proofstate conformance --export ./proofstate-conformance-v1alpha1
```

ProofState verifies every digest before it creates the destination. The parent
directory must exist and the destination itself must not exist. Export uses
exclusive file creation and will not replace an existing file or directory.

The result uses `proofstate.dev/conformance-result/v1alpha1`. Every case reports
its identifier, expected classification, observed classification, and verdict.
The command exits with status `0` only when every fixture has its manifest-pinned
SHA-256 digest and produces the declared classification.

`expected-results.json` contains the exact portable result an independent
implementation must produce. Its digest is pinned by `manifest.json`; the
installed verifier validates both files before reading cases. The generator is
deterministic:

```sh
uv run python scripts/generate_conformance.py --check
```

The generator owns all fixture bytes, manifest hashes, and expected outcomes.
The earlier `conformance-manifest/v1alpha1` remains the 17-case bundle published
with ProofState 0.4.0. Existing case identifiers and outcomes are append-only
within the new manifest schema. An incompatible meaning change requires another
manifest schema version.

## Classifications

- `valid`: the document passes its closed Pydantic schema.
- `invalid_document`: bounded UTF-8 JSON or YAML parsing fails.
- `invalid_scorecard`: parsing succeeds but scorecard validation fails.
- `invalid_attestation`: parsing succeeds but attestation validation fails.

The conformance bundle validates document semantics. It does not create a Git
repository or claim to cover runtime evidence evaluation. Evaluation fixtures
remain roadmap work and will be added without changing existing case outcomes.

## Rule coverage

The two valid scorecards cover defaults and every evidence/check shape. Focused
negative cases then isolate each validation rule family:

| Rule family | Passing fixture | Fail-closed fixtures |
| --- | --- | --- |
| Closed schema and version | `scorecard-valid-minimal`, `attestation-valid` | `*-unknown-field`, `*-invalid-schema-version` |
| Repository identity and object ID | both valid fixtures | empty/long repository identity, invalid commit |
| Scorecard settings and assertion cardinality | `scorecard-valid-complete` | settings below/above bounds, empty/too-many assertions |
| Assertion identifiers, title, severity, and cap | both valid scorecards | invalid/long ID, empty/long title, invalid severity/cap |
| Dependency graph | `scorecard-valid-complete` | self, duplicate, unknown, duplicate-ID, and cycle cases |
| Evidence set and discriminator | both valid scorecards | empty/oversized sets and invalid evidence type |
| Repository path and SHA-256 | both valid scorecards | invalid path and digest cases |
| Pytest symbol evidence | `scorecard-valid-complete` | invalid path, symbol, and framework cases |
| Artifact evidence and checks | `scorecard-valid-complete` | invalid format/cardinality/pointer/operator expectation cases |
| Restricted JSON/YAML documents | YAML Core fixture | duplicate keys, legacy numeric spellings, merge key, non-finite, non-string key |
| Attestation identity and timestamps | `attestation-valid` | empty/long identity, malformed/offsetless timestamps, invalid window |
| Attestation scope | `attestation-valid` | empty/long repository, invalid commit, empty/oversized/invalid/duplicate assertion IDs |
| Attestation statement | `attestation-valid` | empty and over-limit statement |

Installed fixture paths are single filenames, case identifiers are unique, and
each file is limited to one MiB. A missing, oversized, or digest-mismatched file
causes the bundle verdict to fail closed. Reads stop after at most one MiB plus
one byte, before parsing or hashing.
