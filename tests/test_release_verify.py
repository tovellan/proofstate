from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from scripts.verify_release import SEMVER, source_version, verify_artifacts, verify_tag


def test_current_source_versions_agree() -> None:
    root = Path(__file__).parents[1]

    version, failures = source_version(root)

    assert version == "0.3.4"
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
