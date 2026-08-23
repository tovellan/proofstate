# Error reference

Repository and input failures exit with status 2 and a stable code.

| Code | Meaning |
| --- | --- |
| `PS001_INVALID_ARGUMENT` | A path, revision, or argument is invalid. |
| `PS002_NOT_A_GIT_REPOSITORY` | `--repo` is not inside a Git worktree. |
| `PS003_GIT_COMMAND_FAILED` | Git object access failed. |
| `PS004_SCORECARD_NOT_FOUND` | The scorecard is not a regular file at the scorecard revision. |
| `PS005_SCORECARD_TOO_LARGE` | The scorecard exceeds 1 MiB. |
| `PS006_INVALID_DOCUMENT` | The document is malformed or violates the bounded JSON/YAML input profile. |
| `PS007_INVALID_SCORECARD` | The document does not conform to the scorecard schema. |
| `PS008_UNRESOLVABLE_COMMIT` | The pinned commit is missing or has the wrong object format. |
| `PS009_UNRELATED_COMMIT` | The evidence commit is not an ancestor of the scorecard revision. |
| `PS010_UNSUPPORTED_OBJECT_FORMAT` | Git reports an unsupported object format. |
| `PS011_INVALID_TIME` | The evaluation time is invalid or lacks an offset. |
| `PS012_CONFORMANCE_EXPORT_FAILED` | The verified fixture corpus could not be exported safely. |

Evidence failures are represented inside a completed evaluation. `PSE000` is a
verified item; `PSE2xx` covers test symbols, `PSE3xx` structured artifacts,
`PSE4xx` attestations, and `PSE900` a fail-closed internal evidence verification
error. `PSE1xx` covers source access and cross-type evaluation resource limits:

| Code | Meaning |
| --- | --- |
| `PSE101_FILE_MISSING` | Evidence is absent or is not a regular file. |
| `PSE102_FILE_TOO_LARGE` | One evidence source exceeds its byte limit. |
| `PSE103_DIGEST_MISMATCH` | Content does not match its pinned SHA-256 digest. |
| `PSE104_EVALUATION_LIMIT` | A cumulative evidence input, structured parsing, or Git tree lookup budget is exhausted. |

A malformed structured source is charged the full 125,000-node per-document
allowance when applying the cumulative parsing budget.
