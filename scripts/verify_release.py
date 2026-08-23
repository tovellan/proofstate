"""Fail closed when release tags, sources, notes, and artifacts disagree."""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
git_path = shutil.which("git")
if git_path is None:
    raise RuntimeError("git is required")
GIT: str = git_path


def source_version(root: Path) -> tuple[str, list[str]]:
    failures: list[str] = []
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = project["project"]["version"]
    if not isinstance(project_version, str) or not SEMVER.fullmatch(project_version):
        failures.append("pyproject.toml project version is not a plain semantic version")
        project_version = str(project_version)

    module = ast.parse((root / "src/proofstate/__init__.py").read_text(encoding="utf-8"))
    package_version: str | None = None
    for statement in module.body:
        if (
            isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in statement.targets
            )
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            package_version = statement.value.value
    if package_version != project_version:
        failures.append("package and project versions disagree")
    notes = root / "docs" / "release-notes" / f"{project_version}.md"
    if not notes.is_file():
        failures.append(f"release notes are missing for {project_version}")
    return project_version, failures


def verify_tag(root: Path, tag: str, version: str) -> list[str]:
    failures: list[str] = []
    if tag != f"v{version}":
        failures.append("release tag does not match the source version")
        return failures

    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [GIT, "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    object_type = git("cat-file", "-t", tag)
    if object_type.returncode != 0 or object_type.stdout.strip() != "tag":
        failures.append("release tag must exist and be annotated")
        return failures
    tag_commit = git("rev-parse", f"{tag}^{{commit}}")
    head_commit = git("rev-parse", "HEAD")
    if (
        tag_commit.returncode != 0
        or head_commit.returncode != 0
        or tag_commit.stdout.strip() != head_commit.stdout.strip()
    ):
        failures.append("release tag does not identify the checked-out commit")
    return failures


def _wheel_version(path: Path) -> tuple[str | None, str | None]:
    with zipfile.ZipFile(path) as archive:
        metadata_paths = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            return None, None
        metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
    return metadata.get("Name"), metadata.get("Version")


def _sdist_version(path: Path) -> str | None:
    with tarfile.open(path, mode="r:gz") as archive:
        projects = [
            member for member in archive.getmembers() if member.name.endswith("/pyproject.toml")
        ]
        if len(projects) != 1:
            return None
        extracted = archive.extractfile(projects[0])
        if extracted is None:
            return None
        project = tomllib.loads(extracted.read().decode("utf-8"))
    value = project.get("project", {}).get("version")
    return value if isinstance(value, str) else None


def verify_artifacts(directory: Path, version: str) -> list[str]:
    failures: list[str] = []
    wheels = list(directory.glob("proofstate-*.whl"))
    sdists = list(directory.glob("proofstate-*.tar.gz"))
    if len(wheels) != 1 or wheels[0].name != f"proofstate-{version}-py3-none-any.whl":
        failures.append("distribution directory must contain the exact versioned wheel")
    else:
        name, wheel_version = _wheel_version(wheels[0])
        if name != "proofstate" or wheel_version != version:
            failures.append("wheel metadata does not match the source version")
    if len(sdists) != 1 or sdists[0].name != f"proofstate-{version}.tar.gz":
        failures.append("distribution directory must contain the exact versioned source archive")
    elif _sdist_version(sdists[0]) != version:
        failures.append("source archive metadata does not match the source version")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="annotated release tag to verify")
    parser.add_argument("--dist", type=Path, help="distribution directory to verify")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version, failures = source_version(root)
    if arguments.tag:
        failures.extend(verify_tag(root, arguments.tag, version))
    if arguments.dist:
        failures.extend(verify_artifacts(arguments.dist, version))
    if failures:
        for failure in failures:
            print(failure)
        raise SystemExit(1)
    print(f"release inputs agree on ProofState {version}")


if __name__ == "__main__":
    main()
