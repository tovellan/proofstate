# Release process

Only maintainers with repository write access can create a release.

1. Update `CHANGELOG.md` and the release notes.
2. Run `make gate` from a clean checkout.
3. Confirm the version in `pyproject.toml` and `proofstate.__version__` match.
4. Create and push a signed or annotated `vX.Y.Z` tag on a commit already
   present on `main`.
5. Run the `Release` workflow manually with the exact tag and confirmation
   input.
6. Inspect the completed workflow and release assets.

The workflow verifies exact archive membership and source-byte parity, then
clean-installs and exercises both the wheel and source archive before attaching
them to a GitHub release. It does not publish to PyPI, a container registry, or
another package service.
