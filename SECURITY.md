# Security policy

## Supported versions

Security fixes are provided for the latest released minor version. Before a
1.0 release, an upgrade may also include documented schema changes.

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| Earlier | No |

## Report a vulnerability

Use GitHub's private vulnerability reporting flow in the Security tab. Do not
open a public issue for a suspected vulnerability.

Include the affected version, operating system, repository setup, expected
behavior, observed behavior, and a minimal synthetic reproduction. Do not send
real credentials, private source, personal data, or sensitive evidence.

Maintainers will acknowledge a report within five business days, assess impact,
and coordinate a fix and disclosure. Timelines depend on severity and the need
to preserve compatibility.

## Security scope

Relevant issues include evidence accepted from the wrong Git tree, path
traversal, ambiguous parsing, scope bypass, expiration bypass, unsafe command
execution, unbounded input handling, or machine-readable output that marks an
unverified assertion as passing.

Identity authentication and commit-signature verification are documented
limitations, not vulnerabilities, unless the implementation behaves differently
from the published threat model.
