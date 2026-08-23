# Release process

Only maintainers with repository write access can create a release.

The organization owner must select this repository for immutable-release
enforcement before a release starts. Repository administrators cannot disable
that owner-enforced prerequisite. The workflow token deliberately has no
Administration access and does not attempt to read the organization setting.

1. Update `CHANGELOG.md` and the release notes.
2. Run `make gate` from a clean checkout.
3. Confirm the version in `pyproject.toml` and `proofstate.__version__` match.
4. Create and push an annotated `vX.Y.Z` tag on the exact current `main`
   commit. Use the generic maintainer identity and the exact annotation
   `ProofState X.Y.Z`.
5. Run the `Release` workflow from `main` with the exact tag and confirmation
   input. The workflow rejects a dispatch SHA that differs from the tag commit.
6. Inspect the completed workflow and release assets.

The workflow verifies exact archive membership and source-byte parity, then
clean-installs and exercises both the wheel and source archive. It records SLSA
build provenance, creates a draft, attaches both distributions, and publishes
the draft. After publication it requires GitHub to report the release immutable
and cryptographically verifies GitHub's automatic release attestation against
the exact tag, commit, asset names, sizes, and SHA-256 digests. Provenance
records are public through GitHub's attestation store and the Sigstore
transparency log. They establish artifact origin and workflow identity, not
reproducibility or artifact safety.

Verify the immutable release, each downloaded asset, and its explicit build
provenance:

```sh
gh release verify vX.Y.Z --repo tovellan/proofstate
gh release verify-asset vX.Y.Z ./proofstate-X.Y.Z-py3-none-any.whl \
  --repo tovellan/proofstate
gh attestation verify ./proofstate-X.Y.Z-py3-none-any.whl \
  --repo tovellan/proofstate \
  --predicate-type https://slsa.dev/provenance/v1 \
  --signer-workflow tovellan/proofstate/.github/workflows/release.yml \
  --source-ref refs/heads/main \
  --source-digest FULL_RELEASE_COMMIT_SHA
```

The release workflow does not publish to PyPI, a container registry, or another
package service.
