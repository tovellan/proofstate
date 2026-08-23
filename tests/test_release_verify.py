from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from typing import Any, cast

import yaml

from scripts.verify_release import (
    SEMVER,
    source_version,
    verify_artifacts,
    verify_tag,
    verify_tag_object,
)


def test_current_source_versions_agree() -> None:
    root = Path(__file__).parents[1]

    version, failures = source_version(root)

    assert version == "0.4.0"
    assert failures == []


def test_semver_rejects_ambiguous_release_versions() -> None:
    assert SEMVER.fullmatch("0.2.1")
    assert not SEMVER.fullmatch("01.2.1")
    assert not SEMVER.fullmatch("0.2.1rc1")


def test_tag_verification_rejects_mismatch_and_missing_tag() -> None:
    root = Path(__file__).parents[1]

    assert verify_tag(root, "v0.2.0", "0.2.1") == ["release tag does not match the source version"]
    assert verify_tag(root, "v999.999.999", "999.999.999") == [
        "release tag must exist and be annotated"
    ]


def test_tag_object_requires_generic_tagger_and_exact_message() -> None:
    valid = (
        "object " + ("a" * 40) + "\n"
        "type commit\n"
        "tag v0.4.0\n"
        "tagger Tovellan Maintainers <tovellan@users.noreply.github.com> "
        "1787521514 +0530\n\n"
        "ProofState 0.4.0\n"
    )

    assert verify_tag_object(valid, "v0.4.0", "0.4.0") == []
    assert verify_tag_object(
        valid.replace("Tovellan Maintainers", "Named Person"), "v0.4.0", "0.4.0"
    )
    assert verify_tag_object(
        valid + "Co-authored-by: Named Person <person@example.invalid>\n", "v0.4.0", "0.4.0"
    )
    assert verify_tag_object(
        valid.replace("object " + ("a" * 40), "object HEAD"), "v0.4.0", "0.4.0"
    )


def test_artifact_verification_reads_both_metadata_formats(tmp_path: Path) -> None:
    wheel = tmp_path / "proofstate-0.2.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr(
            "proofstate-0.2.1.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: proofstate\nVersion: 0.2.1\n",
        )
    sdist = tmp_path / "proofstate-0.2.1.tar.gz"
    project = b'[project]\nname = "proofstate"\nversion = "0.2.1"\n'
    with tarfile.open(sdist, mode="w:gz") as archive:
        info = tarfile.TarInfo("proofstate-0.2.1/pyproject.toml")
        info.size = len(project)
        archive.addfile(info, io.BytesIO(project))

    assert verify_artifacts(tmp_path, "0.2.1") == []


def test_artifact_verification_fails_on_stale_version(tmp_path: Path) -> None:
    (tmp_path / "proofstate-0.2.0-py3-none-any.whl").touch()
    (tmp_path / "proofstate-0.2.0.tar.gz").touch()

    failures = verify_artifacts(tmp_path, "0.2.1")

    assert len(failures) == 2


def test_release_workflow_binds_and_attests_exact_artifacts() -> None:
    root = Path(__file__).parents[1]
    workflow = cast(
        dict[str, Any],
        yaml.safe_load((root / ".github/workflows/release.yml").read_text(encoding="utf-8")),
    )
    job = workflow["jobs"]["release"]

    assert job["if"] == "${{ inputs.confirm }}"
    assert job["runs-on"] == "ubuntu-latest"
    assert job["env"] == {"RELEASE_TAG": "${{ inputs.tag }}"}
    assert job["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    steps = job["steps"]
    names = [step["name"] for step in steps]
    checkout = steps[names.index("Check out the requested tag")]
    identity = steps[names.index("Validate release identity")]["run"]
    preflight = steps[names.index("Preflight source tree")]["run"]
    source_validation = steps[names.index("Validate source and tests")]["run"]
    attestation = steps[names.index("Attest release distributions")]
    release = steps[names.index("Create draft release without publishing packages")]["run"]
    published = steps[names.index("Verify immutable release and automatic attestation")]["run"]

    for critical_name in {
        "Check out the requested tag",
        "Validate release identity",
        "Preflight source tree",
        "Validate source and tests",
        "Attest release distributions",
        "Create draft release without publishing packages",
        "Verify immutable release and automatic attestation",
    }:
        critical_step = steps[names.index(critical_name)]
        assert "continue-on-error" not in critical_step
        assert "if" not in critical_step
        assert "shell" not in critical_step

    assert checkout["uses"] == ("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1")
    assert checkout["with"] == {
        "ref": "${{ inputs.tag }}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    assert names.index("Check out the requested tag") < names.index("Validate release identity")
    assert names.index("Validate release identity") < names.index("Preflight source tree")
    assert names.index("Preflight source tree") < names.index("Install uv and Python")
    assert names.index("Verify both installed distributions") < names.index(
        "Attest release distributions"
    )
    assert names.index("Attest release distributions") < names.index(
        "Create draft release without publishing packages"
    )
    assert names.index("Create draft release without publishing packages") < names.index(
        "Verify immutable release and automatic attestation"
    )
    assert identity == (
        'test "$GITHUB_REF" = "refs/heads/main"\n'
        'tag_ref="refs/tags/$RELEASE_TAG"\n'
        'test "$(git cat-file -t "$tag_ref")" = "tag"\n'
        'test "$(git rev-parse "$tag_ref^{commit}")" = "$GITHUB_SHA"\n'
        'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"\n'
        "git fetch --force --no-tags origin "
        "'+refs/heads/main:refs/remotes/origin/main'\n"
        'git merge-base --is-ancestor "$GITHUB_SHA" refs/remotes/origin/main\n'
    )
    assert preflight == (
        "python3 scripts/check_repository.py\npython3 scripts/check_distribution.py --source-only\n"
    )
    assert source_validation.splitlines()[0] == (
        'python3 scripts/verify_release.py --tag "$RELEASE_TAG"'
    )
    assert "|| true" not in source_validation
    assert attestation["uses"] == ("actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6")
    assert attestation["with"] == {
        "subject-path": (
            "${{ github.workspace }}/dist/proofstate-*.whl\n"
            "${{ github.workspace }}/dist/proofstate-*.tar.gz\n"
        ),
        "push-to-registry": False,
    }
    assert 'gh release create "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY" \\\n' in release
    assert "  --draft" in release
    assert 'gh release upload "$RELEASE_TAG" "$wheel" "$sdist"' in release
    assert 'gh release edit "$RELEASE_TAG" --draft=false' in release
    assert 'gh release verify "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY"' in published
    assert 'gh release verify-asset "$RELEASE_TAG" "$wheel"' in published
    assert 'gh release verify-asset "$RELEASE_TAG" "$sdist"' in published
    assert "scripts/verify_published_release.py" in published
    assert "Administration" not in published
