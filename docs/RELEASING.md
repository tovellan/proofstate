# Release process

Only maintainers with repository write access can create a release.

1. Update `CHANGELOG.md` and the release notes.
2. Run `make gate` from a clean checkout.
3. Confirm the version in `pyproject.toml` and `proofstate.__version__` match.
4. Create and push a signed or annotated `vX.Y.Z` tag on the exact current
   `main` commit.
5. Run the `Release` workflow from `main` with the exact tag and confirmation
   input. The workflow rejects a dispatch SHA that differs from the tag commit.
6. Inspect the completed workflow and release assets.

The workflow verifies exact archive membership and source-byte parity, then
clean-installs and exercises both the wheel and source archive before attaching
them to a GitHub release. It records SLSA build provenance for both artifacts
before release creation. Provenance records are public through GitHub's
attestation store and the Sigstore transparency log. They establish artifact
origin and workflow identity, not reproducibility or artifact safety.

Verify the immutable release, each downloaded asset, and its explicit build
provenance:

```sh
gh release verify vX.Y.Z --repo tovellan/proofstate
gh release verify-asset vX.Y.Z ./proofstate-X.Y.Z-py3-none-any.whl \
  --repo tovellan/proofstate
gh attestation verify ./proofstate-X.Y.Z-py3-none-any.whl \
  --repo tovellan/proofstate \
  --signer-workflow tovellan/proofstate/.github/workflows/release.yml \
  --source-ref refs/heads/main
```

The release workflow does not publish to PyPI, a container registry, or another
package service.
