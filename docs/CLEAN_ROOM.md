# Clean-room design record

ProofState was independently designed from public specifications and project
documentation. The implementation and examples use synthetic data.

## Primary sources reviewed

- [OpenSSF Scorecard](https://github.com/ossf/scorecard) evaluates repository
  security practices and produces heuristic check scores.
- [in-toto getting started](https://in-toto.io/docs/getting-started/) describes
  signed layouts and link metadata for software supply-chain steps.
- [SLSA provenance](https://slsa.dev/spec/v1.2/provenance) defines verifiable
  information about where, when, and how an artifact was produced.
- [Sigstore policy-controller](https://docs.sigstore.dev/policy-controller/overview/)
  validates signatures and attestations for Kubernetes admission.
- [Witness policy concepts](https://github.com/in-toto/witness/blob/main/docs/concepts/policy.md)
  combine trusted functionaries, attestations, and policy evaluation.
- [CUE](https://github.com/cue-lang/cue) validates and constrains structured
  configuration data.
- [RFC 6901](https://www.rfc-editor.org/rfc/rfc6901) defines JSON Pointer.
- [Git object formats](https://git-scm.com/docs/hash-function-transition/)
  describe SHA-1 and SHA-256 repositories.

## Differentiation decision

The reviewed projects solve adjacent problems. OpenSSF Scorecard measures a
fixed set of security heuristics. in-toto, SLSA, Sigstore, and Witness focus on
signed supply-chain metadata, provenance, and trusted functionaries. CUE is a
general data and constraint language.

ProofState is narrower: it binds organization-defined readiness assertions to
files, test symbols, structured results, and time-bounded human declarations in
related Git trees. It calculates a dependency-aware release gate. It does not
replace the reviewed systems and can consume their bounded output as a
structured artifact.

No private source, benchmark material, internal workflow, customer data,
personal data, credentials, or proprietary scoring logic was used.
