# Compatibility policy

ProofState supports CPython 3.11, 3.12, 3.13, and 3.14 on operating systems with
Git available. CI exercises Linux; local validation also exercises macOS.

Patch releases preserve the documented Python API, CLI commands, exit statuses,
and `v1alpha1` fields unless a security issue makes preservation unsafe. The
`v1alpha1` schema can change in a minor release. Changes will be recorded in the
changelog and should include a migration example.

Unknown fields are errors by design. A consumer should reject schema versions it
does not recognize and pin ProofState when a release gate depends on exact
behavior.

## 0.4.0 YAML and pointer migration

Version 0.4.0 keeps the `v1alpha1` schema identifiers but adopts YAML 1.2 Core
scalar resolution on PyYAML 6.x, restricted to JSON-compatible values. It does
not implement the full YAML 1.2 grammar, and YAML directives are rejected.

| Earlier plain scalar or pointer | 0.4.0 behavior | Migration |
| --- | --- | --- |
| `yes`, `no`, `on`, `off` | String | Use `true` or `false` for a boolean. |
| `012` | Decimal integer `12` | Use `0o12` for octal or quote text. |
| `0b10`, `1:20`, `1_000` | String | Rewrite as a decimal number when numeric meaning is intended. |
| `1e3` | Floating-point number | Quote it when text is intended. |
| Timestamp-looking scalar | String | No migration is required. |
| Plain `<<` mapping key | Rejected | Quote `"<<"` when it is an ordinary string key. |
| `%YAML` or another directive | Rejected | Remove the directive and use the documented restricted profile. |
| Array pointer token `/01` | Not an array index | Use `/1`; `/01` remains valid when traversing an object. |
| Structured document over 125,000 nodes | Rejected | Reduce or split the document; mapping keys and values each count as nodes. |
| Test-symbol source over 64 KiB | Rejected before AST parsing | Split the module or use file and result-artifact evidence. |
| Decimal integer over 4,300 digits | Rejected in JSON and YAML | Shorten it or encode an opaque identifier as a quoted string. |
| Evaluation over 256 uncached regular sources or 10 MiB total | Later sources fail with `PSE104` | Split the scorecard or reduce evidence inputs. |
| Encoded evidence path at or above 16 KiB | Rejected with `PSE104` before Git tree lookup | Shorten the repository path. |
| Encoded scorecard path at or above 16 KiB | Rejected with `PS001` before Git tree lookup | Shorten the repository path. |
| Artifact or attestation parsing over 1,000,000 cumulative charged nodes | That parser latches closed with `PSE104`; a failed parse is charged 125,000 nodes | Reduce, fix, or split structured evidence. |

Array indexes now accept only `0` or a nonzero ASCII digit followed by ASCII
digits. Unicode digits are never array indexes but remain valid object-key text.
Artifact check expectations must be finite JSON-compatible values. Numeric
operators reject booleans and numeric-looking strings.

Only the latest minor version receives routine security fixes before 1.0.
