# Dependency and license review

Runtime dependencies are intentionally small.

| Dependency | Purpose | License | Compatibility |
| --- | --- | --- | --- |
| Pydantic | Closed typed models and JSON Schema | MIT | Compatible with Apache-2.0 |
| PyYAML | Safe YAML scanning and parsing | MIT | Compatible with Apache-2.0 |

The build backend is Hatchling under MIT. Development tools are not distributed
as runtime dependencies. The lockfile records resolved transitive versions. The
source archive contains only package source, build metadata, the license, the
README, and the repository's build-ignore rules. CI rejects duplicate or
special archive entries, verifies wheel `RECORD`, and requires packaged source
bytes to match the reviewed tree before installing both distribution formats.

No dependency requires attribution through a project `NOTICE` file, so the
distribution does not include one. This review should be repeated when runtime
or build dependencies change.

`pip-audit` checks the locked runtime dependency export during the local gate and
CI. An audit result is time-sensitive and is evidence for that run, not a claim
that future vulnerabilities cannot exist.
