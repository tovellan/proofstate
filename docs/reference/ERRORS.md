# Error reference

Repository and input failures exit with status 2 and a stable code.

| Code | Meaning |
| --- | --- |
| `PS001_INVALID_ARGUMENT` | A path, revision, or argument is invalid. |
| `PS002_NOT_A_GIT_REPOSITORY` | `--repo` is not inside a Git worktree. |
| `PS003_GIT_COMMAND_FAILED` | Git object access failed. |
| `PS004_SCORECARD_NOT_FOUND` | The scorecard is not a regular file at the scorecard revision. |
| `PS005_SCORECARD_TOO_LARGE` | The scorecard exceeds 1 MiB. |
| `PS006_INVALID_DOCUMENT` | The document is malformed or uses a rejected YAML feature. |
| `PS007_INVALID_SCORECARD` | The document does not conform to the scorecard schema. |
| `PS008_UNRESOLVABLE_COMMIT` | The pinned commit is missing or has the wrong object format. |
| `PS009_UNRELATED_COMMIT` | The evidence commit is not an ancestor of the scorecard revision. |
| `PS010_UNSUPPORTED_OBJECT_FORMAT` | Git reports an unsupported object format. |
| `PS011_INVALID_TIME` | The evaluation time is invalid or lacks an offset. |

Evidence failures are represented inside a completed evaluation. `PSE000` is a
verified item; `PSE1xx` covers files, `PSE2xx` test symbols, `PSE3xx` structured
artifacts, `PSE4xx` attestations, and `PSE900` a fail-closed internal evidence
verification error.
