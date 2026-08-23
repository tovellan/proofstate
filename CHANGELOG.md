# Changelog

All notable changes are recorded here. The format follows Keep a Changelog and
versions follow Semantic Versioning.

## [Unreleased]

### Changed

- Pinned GitHub Actions to current Node 24 releases and isolated cache writers
  across concurrent CI jobs.
- Limited distribution archives to reviewed package files and added the PEP 561
  marker for downstream type checkers.
- Bound release notes to the exact semantic version tag selected by a
  maintainer.

## [0.1.0] - 2026-08-24

### Added

- Versioned `v1alpha1` scorecard, attestation, result, and error formats.
- Git-tree verification for files, pytest symbols, and structured artifacts.
- Scoped, expiring human attestations stored at the scorecard revision.
- Dependency-aware assertions with severity and failure-cap gate semantics.
- Text and JSON CLI output with stable exit status behavior.
- Adversarial, property-based, integration, and bounded performance tests.
- Hardened CI, CodeQL, secret scanning, dependency updates, and release workflow.

[Unreleased]: https://github.com/tovellan/proofstate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tovellan/proofstate/releases/tag/v0.1.0
