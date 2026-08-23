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

Only the latest minor version receives routine security fixes before 1.0.
