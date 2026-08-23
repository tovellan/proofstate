# Architecture

ProofState has four layers with one-way dependencies.

1. `models.py` defines closed Pydantic models for scorecards and attestations.
2. `git.py` resolves commits and reads regular-file blobs without consulting the
   working tree.
3. `evidence.py` verifies machine evidence and human attestation scope.
4. `evaluate.py` orders assertions, propagates dependency failures, calculates
   the achieved gate, and returns a result value. `cli.py` is a thin adapter.

## Two commits, two purposes

The evidence commit identifies the source and machine artifacts under review.
It must be a full object ID and an ancestor of the scorecard revision.

The scorecard revision contains the policy and human attestation files. This
separation avoids an impossible self-reference: a file inside a commit cannot
contain that commit's final object ID. An attestation in a later commit can scope
itself to the earlier evidence commit.

Both revisions are resolved to immutable commits. The result records the commit
and tree IDs for each.

## Evaluation sequence

1. Discover the Git worktree from `--repo`.
2. Resolve `--scorecard-ref` and read the scorecard blob from that commit.
3. Parse bounded UTF-8 YAML or JSON with duplicate keys, non-finite numbers,
   non-string mapping keys, nonstandard JSON constants, aliases, anchors, and
   explicit YAML tags rejected. YAML timestamps remain strings for schema-level
   RFC 3339 validation.
4. Validate the closed schema and dependency graph.
5. Resolve the full evidence commit and prove it is an ancestor of the scorecard
   commit.
6. Evaluate dependencies first, then verify every evidence item.
7. Mark a dependent assertion `blocked` when any dependency is not passing.
8. Apply the lowest failure cap and compare it with the requested gate.

No evidence check executes repository code or follows a symlink.

## Determinism

Given the same Git object database, scorecard revision, required gate, and
evaluation time, the result is deterministic. The default evaluation time is the
current UTC time because attestation expiration must be enforced. Use `--at` for
reproducible historical verification.

JSON output sorts keys. Assertion order follows the scorecard, while dependency
evaluation is topological.

## Resource bounds

- Scorecards are limited to 1 MiB.
- Evidence items default to 1 MiB and can be configured up to 10 MiB.
- Scorecards support at most 1,000 assertions.
- Each assertion supports at most 100 dependencies and 100 items per evidence
  category.
- Structured artifacts support at most 100 checks.
- Structured documents are limited to 100 nesting levels.
- Git subprocesses have a 30-second timeout.
