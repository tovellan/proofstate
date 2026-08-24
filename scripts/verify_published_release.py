"""Verify GitHub's immutable release record against local distributions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
RELEASE_PREDICATE = "https://in-toto.io/attestation/release/v0.2"
RELEASE_SIGNER = "https://dotcom.releases.github.com"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def verify_remote_tag(
    tag_ref: object,
    tag_object: object,
    *,
    tag: str,
    tag_object_id: str,
    commit: str,
) -> list[str]:
    version = tag.removeprefix("v")
    if tag != f"v{version}" or SEMVER.fullmatch(version) is None:
        return ["release tag is not a plain semantic version"]
    for label, object_id in (("tag object", tag_object_id), ("commit", commit)):
        if len(object_id) != 40 or any(
            character not in "0123456789abcdef" for character in object_id
        ):
            return [f"release {label} is not a full lowercase SHA-1 object ID"]

    ref_data = _mapping(tag_ref)
    ref_object = _mapping(ref_data.get("object"))
    remote_tag = _mapping(tag_object)
    remote_tagger = _mapping(remote_tag.get("tagger"))
    remote_target = _mapping(remote_tag.get("object"))
    if (
        ref_data.get("ref") != f"refs/tags/{tag}"
        or ref_object.get("type") != "tag"
        or ref_object.get("sha") != tag_object_id
        or remote_tag.get("sha") != tag_object_id
        or remote_tag.get("tag") != tag
        or remote_tag.get("message") != f"ProofState {version}\n"
        or remote_tagger.get("name") != "Tovellan Maintainers"
        or remote_tagger.get("email") != "tovellan@users.noreply.github.com"
        or remote_target.get("type") != "commit"
        or remote_target.get("sha") != commit
    ):
        return ["remote annotated tag does not match the release contract"]
    return []


def verify_published_release(
    release: object,
    attestation: object,
    tag_ref: object,
    tag_object: object,
    *,
    tag: str,
    tag_object_id: str,
    commit: str,
    repository: str,
    dist: Path,
) -> list[str]:
    failures: list[str] = []
    version = tag.removeprefix("v")
    if tag != f"v{version}" or SEMVER.fullmatch(version) is None:
        return ["published release tag is not a plain semantic version"]
    failures.extend(
        verify_remote_tag(
            tag_ref,
            tag_object,
            tag=tag,
            tag_object_id=tag_object_id,
            commit=commit,
        )
    )
    if failures:
        return failures

    wheel = dist / f"proofstate-{version}-py3-none-any.whl"
    sdist = dist / f"proofstate-{version}.tar.gz"
    expected_files = {path.name: path for path in (wheel, sdist)}
    if not all(path.is_file() for path in expected_files.values()):
        return ["published release distributions are missing"]
    expected_digests = {name: _sha256(path) for name, path in expected_files.items()}

    release_data = _mapping(release)
    if (
        release_data.get("tag_name") != tag
        or release_data.get("name") != f"ProofState {version}"
        or release_data.get("draft") is not False
        or release_data.get("prerelease") is not False
        or release_data.get("immutable") is not True
    ):
        failures.append("GitHub release identity or immutable state does not match")

    assets = release_data.get("assets")
    asset_data: dict[str, Mapping[str, Any]] = {}
    if isinstance(assets, list):
        for asset_value in assets:
            asset = _mapping(asset_value)
            name = asset.get("name")
            if isinstance(name, str):
                asset_data[name] = asset
    if (
        not isinstance(assets, list)
        or len(assets) != 2
        or set(asset_data) != set(expected_files)
        or len(asset_data) != 2
    ):
        failures.append("GitHub release must contain exactly both distributions")
    else:
        for name, path in expected_files.items():
            asset = asset_data[name]
            if (
                asset.get("state") != "uploaded"
                or asset.get("size") != path.stat().st_size
                or asset.get("digest") != f"sha256:{expected_digests[name]}"
            ):
                failures.append(f"GitHub release asset does not match: {name}")

    result = _mapping(_mapping(attestation).get("verificationResult"))
    certificate = _mapping(_mapping(result.get("signature")).get("certificate"))
    statement = _mapping(result.get("statement"))
    predicate = _mapping(statement.get("predicate"))
    if certificate.get("subjectAlternativeName") != RELEASE_SIGNER:
        failures.append("release attestation was not signed by GitHub's release service")
    if not isinstance(result.get("verifiedTimestamps"), list) or not result.get(
        "verifiedTimestamps"
    ):
        failures.append("release attestation has no verified timestamp")
    if (
        statement.get("_type") != "https://in-toto.io/Statement/v1"
        or statement.get("predicateType") != RELEASE_PREDICATE
        or predicate.get("repository") != repository
        or predicate.get("tag") != tag
        or predicate.get("purl") != f"pkg:github/{repository}@{tag}"
    ):
        failures.append("release attestation predicate does not match")

    expected_subjects: list[object] = [
        {"uri": f"pkg:github/{repository}@{tag}", "digest": {"sha1": tag_object_id}},
        *[
            {"name": name, "digest": {"sha256": expected_digests[name]}}
            for name in sorted(expected_files)
        ],
    ]
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or sorted(
        json.dumps(subject, sort_keys=True) for subject in subjects
    ) != sorted(json.dumps(subject, sort_keys=True) for subject in expected_subjects):
        failures.append("release attestation subjects do not match the commit and distributions")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-json", type=Path)
    parser.add_argument("--attestation-json", type=Path)
    parser.add_argument("--tag-ref-json", type=Path, required=True)
    parser.add_argument("--tag-object-json", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--tag-object", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository")
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--remote-tag-only", action="store_true")
    arguments = parser.parse_args()
    tag_ref = json.loads(arguments.tag_ref_json.read_text(encoding="utf-8"))
    tag_object = json.loads(arguments.tag_object_json.read_text(encoding="utf-8"))
    if arguments.remote_tag_only:
        failures = verify_remote_tag(
            tag_ref,
            tag_object,
            tag=arguments.tag,
            tag_object_id=arguments.tag_object,
            commit=arguments.commit,
        )
        if failures:
            for failure in failures:
                print(failure)
            raise SystemExit(1)
        print(f"remote annotated tag agrees with {arguments.tag}")
        return
    if (
        arguments.release_json is None
        or arguments.attestation_json is None
        or arguments.repository is None
        or arguments.dist is None
    ):
        parser.error(
            "post-publication verification requires release, attestation, repository, and dist"
        )
    release = json.loads(arguments.release_json.read_text(encoding="utf-8"))
    attestation = json.loads(arguments.attestation_json.read_text(encoding="utf-8"))
    failures = verify_published_release(
        release,
        attestation,
        tag_ref,
        tag_object,
        tag=arguments.tag,
        tag_object_id=arguments.tag_object,
        commit=arguments.commit,
        repository=arguments.repository,
        dist=arguments.dist,
    )
    if failures:
        for failure in failures:
            print(failure)
        raise SystemExit(1)
    print(f"immutable GitHub release agrees with {arguments.tag}")


if __name__ == "__main__":
    main()
