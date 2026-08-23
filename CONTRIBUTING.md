# Contributing

ProofState welcomes focused fixes and features that preserve fail-closed
evaluation.

## Before opening a change

Open an issue for schema changes, new evidence types, or changes to gate
semantics. Small bug fixes and documentation corrections can go directly to a
pull request.

Use synthetic repositories, identities, organizations, URLs, and artifacts in
tests and examples. Do not submit credentials, personal data, private repository
details, or proprietary policy material.

## Development setup

```sh
uv sync --locked --all-groups
make lint
make test
```

Before submitting:

```sh
make gate
```

Tests must cover the successful path and at least one failure path. Changes to
the public Python API, CLI, result JSON, scorecard schema, or attestation schema
need matching reference documentation and a changelog entry.

Commits should be small and explain why the change is needed. Create commits as
`Tovellan Maintainers <tovellan-maintainers@users.noreply.github.com>`. Do not
add coauthor, sign-off, generator, or other extra authorship trailers. By
contributing, you agree that your work is licensed under Apache License 2.0.

## Review criteria

Maintainers review correctness, deterministic behavior, compatibility, bounded
resource use, secure defaults, documentation accuracy, and test evidence. A
feature that can turn an unreadable or ambiguous state into a pass will not be
accepted.
