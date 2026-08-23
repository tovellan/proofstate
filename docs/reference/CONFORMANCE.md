# Conformance bundle

ProofState installs a portable `v1alpha1` fixture corpus inside the wheel.
It gives independent parsers a shared set of valid and fail-closed documents.
Version 0.4.0 carries 17 cases, including seven YAML cases that distinguish
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

## Classifications

- `valid`: the document passes its closed Pydantic schema.
- `invalid_document`: bounded UTF-8 JSON or YAML parsing fails.
- `invalid_scorecard`: parsing succeeds but scorecard validation fails.
- `invalid_attestation`: parsing succeeds but attestation validation fails.

The conformance bundle validates document semantics. It does not create a Git
repository or claim to cover runtime evidence evaluation. Evaluation fixtures
remain roadmap work and will be added without changing existing case outcomes.

Installed fixture paths are single filenames, case identifiers are unique, and
each file is limited to one MiB. A missing, oversized, or digest-mismatched file
causes the bundle verdict to fail closed. Reads stop after at most one MiB plus
one byte, before parsing or hashing.
