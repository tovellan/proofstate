from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.verify_published_release import verify_published_release, verify_remote_tag


def _records(
    dist: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    commit = "a" * 40
    tag_object_id = "b" * 40
    assets = []
    subjects: list[object] = [
        {
            "uri": "pkg:github/tovellan/proofstate@v0.4.0",
            "digest": {"sha1": tag_object_id},
        }
    ]
    for path in sorted(dist.iterdir()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assets.append(
            {
                "name": path.name,
                "state": "uploaded",
                "size": path.stat().st_size,
                "digest": f"sha256:{digest}",
            }
        )
        subjects.append({"name": path.name, "digest": {"sha256": digest}})
    release: dict[str, object] = {
        "tag_name": "v0.4.0",
        "name": "ProofState 0.4.0",
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "assets": assets,
    }
    attestation: dict[str, object] = {
        "verificationResult": {
            "signature": {
                "certificate": {"subjectAlternativeName": "https://dotcom.releases.github.com"}
            },
            "verifiedTimestamps": [{"type": "TimestampAuthority"}],
            "statement": {
                "_type": "https://in-toto.io/Statement/v1",
                "predicateType": "https://in-toto.io/attestation/release/v0.2",
                "predicate": {
                    "repository": "tovellan/proofstate",
                    "tag": "v0.4.0",
                    "purl": "pkg:github/tovellan/proofstate@v0.4.0",
                },
                "subject": subjects,
            },
        }
    }
    tag_ref: dict[str, object] = {
        "ref": "refs/tags/v0.4.0",
        "object": {"type": "tag", "sha": tag_object_id},
    }
    tag_object: dict[str, object] = {
        "sha": tag_object_id,
        "tag": "v0.4.0",
        "message": "ProofState 0.4.0\n",
        "tagger": {
            "name": "Tovellan Maintainers",
            "email": "tovellan@users.noreply.github.com",
        },
        "object": {"type": "commit", "sha": commit},
    }
    return release, attestation, tag_ref, tag_object


def test_published_release_requires_exact_immutable_record(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "proofstate-0.4.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "proofstate-0.4.0.tar.gz").write_bytes(b"sdist")
    release, attestation, tag_ref, tag_object = _records(dist)

    assert (
        verify_published_release(
            release,
            attestation,
            tag_ref,
            tag_object,
            tag="v0.4.0",
            tag_object_id="b" * 40,
            commit="a" * 40,
            repository="tovellan/proofstate",
            dist=dist,
        )
        == []
    )


def test_published_release_rejects_drift_and_non_github_attestation(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "proofstate-0.4.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "proofstate-0.4.0.tar.gz").write_bytes(b"sdist")
    release, attestation, tag_ref, tag_object = _records(dist)
    release["immutable"] = False
    result = attestation["verificationResult"]
    assert isinstance(result, dict)
    signature = result["signature"]
    assert isinstance(signature, dict)
    signature["certificate"] = {"subjectAlternativeName": "https://example.invalid"}

    failures = verify_published_release(
        release,
        attestation,
        tag_ref,
        tag_object,
        tag="v0.4.0",
        tag_object_id="b" * 40,
        commit="a" * 40,
        repository="tovellan/proofstate",
        dist=dist,
    )

    assert "GitHub release identity or immutable state does not match" in failures
    assert "release attestation was not signed by GitHub's release service" in failures


def test_remote_tag_check_rejects_a_moved_tag(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "proofstate-0.4.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "proofstate-0.4.0.tar.gz").write_bytes(b"sdist")
    _, _, tag_ref, tag_object = _records(dist)
    target = tag_object["object"]
    assert isinstance(target, dict)
    target["sha"] = "c" * 40

    assert verify_remote_tag(
        tag_ref,
        tag_object,
        tag="v0.4.0",
        tag_object_id="b" * 40,
        commit="a" * 40,
    ) == ["remote annotated tag does not match the release contract"]
