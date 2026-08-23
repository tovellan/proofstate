# Governance

ProofState uses a maintainer-led model during its initial development.

Maintainers merge changes, publish releases, handle security reports, and keep
the schema and compatibility policy coherent. Decisions should be based on
public technical rationale, tests, user impact, and the fail-closed security
contract.

Substantial changes start with an issue. Maintainers seek rough consensus. When
consensus is not possible, the current maintainers make and document the
decision. Security-sensitive discussion may remain private until disclosure is
safe.

Consistent contributors may be invited as maintainers based on sound reviews,
careful implementation, reliable follow-through, and respect for project
policies. Maintainer access can be removed after sustained inactivity, misuse of
access, or conduct violations. No maintainer role is permanent or transferable.

Every change to `main` requires passing status checks and at least one
GitHub-recorded approval from someone other than the last pusher. Stale
approvals are dismissed, conversations must be resolved, and branch protection
also applies to administrators. Emergency security fixes can receive expedited
review, but cannot bypass these controls.
