# Threat model

## Security objective

ProofState must not mark an assertion as passing unless every declared evidence
item can be verified against the intended Git revisions and every dependency is
passing.

## Trusted inputs

- The local Git executable and object database.
- The operating system and Python runtime.
- The caller's selected repository, scorecard path, scorecard revision,
  requested gate, and evaluation time.
- Repository owners who control policy commits and gate configuration.

## Untrusted inputs

- Scorecard, attestation, source, test, and artifact content.
- Repository paths and Git revisions supplied to the CLI.
- Declared attestation identities and statements.
- Uncommitted files in the working tree.

## Controls

- Git commands use argument arrays, no shell, a fixed timeout, and read-only
  object operations.
- Paths are relative POSIX paths; absolute paths, dot segments, `.git`, NUL, and
  backslashes are rejected.
- Evidence is read by object ID from commit trees. Symlinks are not followed.
- Evidence commits use full object IDs and must be ancestors of the scorecard
  revision.
- Inputs are byte-bounded before parsing.
- YAML uses safe constructors and rejects duplicate keys, non-string mapping
  keys, aliases, anchors, and explicit tags. Implicit timestamps remain strings
  until schema validation. JSON duplicate keys are rejected.
- Python tests are parsed with `ast`; repository code is never imported or run.
- Structured checks use a fixed operator set with no expressions or regular
  expressions.
- Attestations require a bounded statement, aware timestamps, an unexpired
  window, and exact scope.
- Missing dependencies and unexpected Git evidence errors fail closed.

## Attacker goals considered

- Edit the working scorecard without committing it.
- Refer to evidence from an unrelated commit in the same object database.
- Use a symlink to point outside the repository.
- Hide a duplicate key in YAML or JSON.
- Use YAML construction tags or aliases to create unsafe or oversized objects.
- Claim a review for another repository, commit, or assertion.
- Reuse an expired or not-yet-valid attestation.
- Confuse booleans and numbers in structured comparisons.
- Cause a dependency to pass after its prerequisite fails.

## Out of scope and limitations

- ProofState does not authenticate the string in `identity`.
- ProofState does not verify commit or tag signatures.
- A repository owner can change policy in a later commit. Consumers must pin the
  scorecard revision they trust.
- Artifact content is verified, not its producing process. Use provenance or a
  trusted CI mechanism when producer identity matters.
- A named test symbol proves presence, not collection or successful execution.
- ProofState does not fetch missing Git objects and may fail in shallow or
  partial clones.
- Denial of service below configured size and count limits is reduced but not
  eliminated.

For cryptographic supply-chain attestations, use a system designed for signing
and provenance, then reference its bounded verification result as a ProofState
artifact.
