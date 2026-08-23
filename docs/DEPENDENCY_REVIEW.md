# Dependency and license review

Runtime dependencies are intentionally small.

| Dependency | Purpose | License | Compatibility |
| --- | --- | --- | --- |
| Pydantic | Closed typed models and JSON Schema | MIT | Compatible with Apache-2.0 |
| PyYAML | Safe YAML scanning and parsing | MIT | Compatible with Apache-2.0 |

The build backend is Hatchling under MIT. Development tools are not distributed
as runtime dependencies. The lockfile records resolved transitive versions.

No dependency requires attribution through a project `NOTICE` file, so version
0.1.0 does not include one. This review should be repeated when runtime or build
dependencies change.

`pip-audit` checks the locked runtime dependency export during the local gate and
CI. An audit result is time-sensitive and is evidence for that run, not a claim
that future vulnerabilities cannot exist.
