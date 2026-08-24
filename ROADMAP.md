# Roadmap

The roadmap records direction, not commitments or delivery dates.

## Near term

- Gather feedback on the `v1alpha1` schema and error taxonomy.
- Extend conformance beyond document schemas with separately versioned Git
  evaluation fixtures, without changing the 64 existing case outcomes.
- Define a plugin boundary for additional bounded machine evidence types.

## Before a stable schema

- Specify schema evolution and deprecation rules.
- Require the trust, revocation, offline, schema, and review gates in the
  authenticated-attestation design before proposing signature implementation.
- Evaluate portable named-test discovery for languages beyond Python.
- Preserve the 64 `conformance-manifest/v1alpha2` case identifiers and outcomes
  as append-only; use a new manifest schema version for an incompatible change.

## Not planned for alpha releases

- Running test suites or arbitrary commands.
- Fetching evidence from network services.
- Acting as an identity provider or certificate authority.
- Replacing in-toto, SLSA provenance, Sigstore, or OpenSSF Scorecard.
