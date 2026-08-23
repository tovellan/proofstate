# Changelog

All notable changes are recorded here. The format follows Keep a Changelog and
versions follow Semantic Versioning.

## [Unreleased]

### Fixed

- Preserved YAML timestamp text for RFC 3339 validation, rejected non-string
  mapping keys, and applied recursive type-exact structured comparisons.

## [0.3.3] - 2026-08-24

### Changed

- Limited distribution archives to reviewed package files and added the PEP 561
  marker for downstream type checkers.

## [0.3.2] - 2026-08-24

### Security

- Rejected ambiguous paths, timestamps, non-finite values, excessive nesting,
  oversized pointer indexes, and non-collectable pytest symbol shapes.
- Applied type-exact membership checks to structured evidence.

## [0.3.1] - 2026-08-24

### Changed

- Updated the reviewed development type-checking range and lock to mypy 2.x.

## [0.3.0] - 2026-08-24

### Added

- Safe export of the exact installed `v1alpha1` conformance corpus.
- Stable fail-closed errors for existing or unavailable export destinations.

## [0.2.1] - 2026-08-24

### Changed

- Required annotated release tags to identify the exact checked-out commit.
- Verified matching source, notes, wheel, and source archive versions before release.
- Isolated local release builds from stale artifacts in the working directory.

## [0.2.0] - 2026-08-24

### Added

- Installed, digest-pinned `v1alpha1` scorecard and attestation conformance fixtures.
- Text and JSON conformance verification with per-case expected outcomes.
- Attestation JSON Schema export through the existing schema command.

### Changed

- Pinned GitHub Actions to current Node 24 releases and isolated cache writers
  across concurrent CI jobs.
- Verified the installed conformance bundle during clean-wheel and release checks.

## [0.1.0] - 2026-08-24

### Added

- Versioned `v1alpha1` scorecard, attestation, result, and error formats.
- Git-tree verification for files, pytest symbols, and structured artifacts.
- Scoped, expiring human attestations stored at the scorecard revision.
- Dependency-aware assertions with severity and failure-cap gate semantics.
- Text and JSON CLI output with stable exit status behavior.
- Adversarial, property-based, integration, and bounded performance tests.
- Hardened CI, CodeQL, secret scanning, dependency updates, and release workflow.

[Unreleased]: https://github.com/tovellan/proofstate/compare/v0.3.3...HEAD
[0.3.3]: https://github.com/tovellan/proofstate/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/tovellan/proofstate/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/tovellan/proofstate/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/tovellan/proofstate/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/tovellan/proofstate/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/tovellan/proofstate/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/tovellan/proofstate/releases/tag/v0.1.0
