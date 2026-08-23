# Conformance bundle

ProofState 0.2.0 installs a portable `v1alpha1` fixture corpus inside the wheel.
It gives independent parsers a shared set of valid and fail-closed documents.

Run the bundled verifier:

```sh
proofstate conformance --format json
```

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
causes the bundle verdict to fail closed.
