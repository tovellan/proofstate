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
3. Parse bounded UTF-8 YAML or JSON into JSON-compatible values. YAML uses 1.2
   Core scalar resolution on PyYAML 6.x; duplicate keys, plain merge keys,
   non-finite numbers, non-string mapping keys, directives, aliases, anchors,
   and explicit tags are rejected. Timestamp-looking scalars remain strings for
   schema-level RFC 3339 validation. This is a restricted input profile, not
   full YAML 1.2 grammar support.
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

JSON Pointer traversal distinguishes object keys from array indexes. Object
tokens preserve their exact decoded text. Array tokens are canonical ASCII
decimal indexes only, preventing alternate spellings such as `01` or Unicode
digits from identifying the same element.

## Resource bounds

- Scorecards are limited to 1 MiB.
- Evidence items default to 1 MiB and can be configured up to 10 MiB.
- One evaluation admits at most 256 uncached regular evidence sources whose
  bytes total at most 10 MiB. Later sources fail closed with `PSE104`.
- Test-symbol Python source has an independent 64 KiB pre-parse limit.
- Parsed symbol indexes are retained for sources admitted by the evaluation
  budget.
- Artifact and attestation parsing each have independent cumulative limits of
  10 MiB of source input and 1,000,000 structured nodes. Exceeding either
  limit latches that parser closed for unseen sources instead of evicting and
  reparsing material. A failed structured parse conservatively consumes the
  full 125,000-node per-document allowance because its exact completed graph
  is unavailable.
- Scorecards support at most 1,000 assertions.
- Each assertion supports at most 100 dependencies and 100 items per evidence
  category.
- Structured artifacts support at most 100 checks.
- Structured documents are limited to 100 nesting levels.
- Structured documents are limited to 125,000 scalar and collection nodes.
  YAML enforces this limit before constructing the document object graph.
- Decimal integers in JSON and YAML are limited to 4,300 digits using a
  library-owned conversion path, independent of Python's mutable process-wide
  integer-string setting.
- Git subprocesses have a 30-second timeout.
- Tree metadata for distinct evidence paths is resolved in deterministic
  batches of at most 256 paths and 16 KiB of path text. Prefix-conflicting
  paths use separate batches so Git cannot expand a directory pathspec while
  resolving a descendant. A single encoded path at or above 16 KiB fails with
  `PSE104` before Git argument construction because the batch budget includes
  its terminating separator; the same user-supplied scorecard path is an
  argument error (`PS001`). An evaluation runs at most 256 tree lookup batches.
