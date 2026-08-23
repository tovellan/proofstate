"""Validate the files carried by built distribution archives."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


class DistributionError(ValueError):
    pass


def _single(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise DistributionError(f"expected one {pattern} archive, found {len(matches)}")
    return matches[0]


def _validate_paths(names: set[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if not name or "\\" in name or path.is_absolute() or ".." in path.parts:
            raise DistributionError(f"unsafe archive path: {name!r}")


def _source_files(root: Path) -> set[str]:
    package = root / "src" / "proofstate"
    return {
        path.relative_to(root).as_posix()
        for path in package.iterdir()
        if path.suffix == ".py" or path.name == "py.typed"
    }


def _check_sdist(archive: Path, source_files: set[str]) -> None:
    with tarfile.open(archive, mode="r:gz") as bundle:
        members = bundle.getmembers()
    invalid = sorted(member.name for member in members if not (member.isdir() or member.isfile()))
    if invalid:
        raise DistributionError(f"source archive contains non-regular entries: {invalid!r}")
    files = {member.name for member in members if member.isfile()}
    _validate_paths(files)
    prefixes = {PurePosixPath(name).parts[0] for name in files}
    if len(prefixes) != 1:
        raise DistributionError("source archive must have one top-level directory")
    prefix = prefixes.pop()
    expected_relative = source_files | {
        ".gitignore",
        "LICENSE",
        "PKG-INFO",
        "README.md",
        "pyproject.toml",
    }
    expected = {f"{prefix}/{name}" for name in expected_relative}
    if files != expected:
        missing = sorted(expected - files)
        unexpected = sorted(files - expected)
        raise DistributionError(
            f"source archive boundary mismatch; missing={missing!r}, unexpected={unexpected!r}"
        )


def _check_wheel(archive: Path, source_files: set[str]) -> None:
    with zipfile.ZipFile(archive) as bundle:
        files = {entry.filename for entry in bundle.infolist() if not entry.is_dir()}
    _validate_paths(files)
    metadata_directories = {
        PurePosixPath(name).parts[0]
        for name in files
        if PurePosixPath(name).parts[0].endswith(".dist-info")
    }
    if len(metadata_directories) != 1:
        raise DistributionError("wheel must have one dist-info directory")
    metadata = metadata_directories.pop()
    package_files = {name.removeprefix("src/") for name in source_files}
    expected = package_files | {
        f"{metadata}/METADATA",
        f"{metadata}/RECORD",
        f"{metadata}/WHEEL",
        f"{metadata}/entry_points.txt",
        f"{metadata}/licenses/LICENSE",
    }
    if files != expected:
        missing = sorted(expected - files)
        unexpected = sorted(files - expected)
        raise DistributionError(
            f"wheel boundary mismatch; missing={missing!r}, unexpected={unexpected!r}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default="dist", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source_files = _source_files(root)
    _check_sdist(_single(arguments.directory, "proofstate-*.tar.gz"), source_files)
    _check_wheel(_single(arguments.directory, "proofstate-*.whl"), source_files)
    print("distribution contents passed")


if __name__ == "__main__":
    main()
