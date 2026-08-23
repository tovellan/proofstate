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
  object operations. Replacement refs are ignored, pathspecs are literal, and
  missing objects cannot trigger a lazy network fetch.
- Paths are relative POSIX paths; absolute paths, dot segments, `.git`, NUL, and
  backslashes are rejected.
- Evidence is read by object ID from commit trees. Symlinks are not followed.
- Evidence commits use full object IDs and must be ancestors of the scorecard
  revision.
- Inputs are byte-bounded before parsing. Installed conformance inputs are read
  only through the limit plus one byte. Structured inputs reject more than
  125,000 scalar and collection nodes; YAML enforces the limit before
  constructing the document object graph.
- YAML uses isolated YAML 1.2 Core scalar resolvers on PyYAML 6.x and safe
  constructors. Duplicate keys, plain merge keys, non-string mapping keys,
  directives, aliases, anchors, explicit tags, and non-JSON values are rejected.
  Implicit timestamps remain strings until schema validation. JSON duplicate
  keys and nonstandard constants are rejected.
- Python tests are parsed with `ast`; repository code is never imported or run.
- Structured checks use a fixed operator set with no expressions or regular
  expressions. Object equality compares key sets once and uses direct key
  lookups. Array traversal accepts only canonical ASCII decimal indexes.
- Attestations require a bounded statement, aware timestamps, an unexpired
  window, and exact scope.
- Dependency validation and evaluation are iterative within the declared
  1,000-assertion limit.
- Missing dependencies and unexpected Git errors for every evidence type fail
  closed.

## Attacker goals considered

- Edit the working scorecard without committing it.
- Refer to evidence from an unrelated commit in the same object database.
- Redirect a named object through a local Git replacement ref.
- Use a symlink to point outside the repository.
- Hide a duplicate key in YAML or JSON.
- Exploit YAML 1.1 scalar ambiguity, merge keys, construction tags, or aliases.
- Address one array element through alternate leading-zero or Unicode indexes.
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
- Repository and distribution gates assume the checked-out source tree is not
  concurrently mutated while they inspect it.
- Denial of service below configured size and count limits is reduced but not
  eliminated.

For cryptographic supply-chain attestations, use a system designed for signing
and provenance, then reference its bounded verification result as a ProofState
artifact.
