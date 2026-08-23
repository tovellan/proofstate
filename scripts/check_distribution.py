"""Validate the files carried by built distribution archives."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import stat
import tarfile
import zipfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path, PurePosixPath


class DistributionError(ValueError):
    pass


MAX_MEMBER_BYTES = 2_097_152
ALLOWED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


def _single(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise DistributionError(f"expected one {pattern} archive, found {len(matches)}")
    return matches[0]


def _validate_paths(names: Iterable[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        parts = name.split("/")
        if (
            not name
            or "\\" in name
            or "\x00" in name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise DistributionError(f"unsafe archive path: {name!r}")


def _reject_duplicate_names(names: list[str], archive_kind: str) -> None:
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise DistributionError(f"{archive_kind} contains duplicate entries: {duplicates!r}")


def _source_files(root: Path) -> dict[str, bytes]:
    package = root / "src" / "proofstate"
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file() and (path.suffix in {".json", ".py"} or path.name == "py.typed")
    }


def _read_tar_payloads(bundle: tarfile.TarFile, members: list[tarfile.TarInfo]) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for member in members:
        if member.size > MAX_MEMBER_BYTES:
            raise DistributionError(f"source archive member is too large: {member.name!r}")
        extracted = bundle.extractfile(member)
        if extracted is None:
            raise DistributionError(f"source archive member is unreadable: {member.name!r}")
        payload = extracted.read(MAX_MEMBER_BYTES + 1)
        if len(payload) != member.size:
            raise DistributionError(f"source archive member size is invalid: {member.name!r}")
        payloads[member.name] = payload
    return payloads


def _check_sdist(archive: Path, source_files: dict[str, bytes]) -> None:
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            members = bundle.getmembers()
            names = [member.name for member in members]
            _reject_duplicate_names(names, "source archive")
            _validate_paths(names)
            invalid = sorted(member.name for member in members if not member.isfile())
            if invalid:
                raise DistributionError(f"source archive contains non-regular entries: {invalid!r}")
            files = set(names)
            prefixes = {PurePosixPath(name).parts[0] for name in files}
            if len(prefixes) != 1:
                raise DistributionError("source archive must have one top-level directory")
            prefix = prefixes.pop()
            expected_relative = set(source_files) | {
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
                    "source archive boundary mismatch; "
                    f"missing={missing!r}, unexpected={unexpected!r}"
                )
            payloads = _read_tar_payloads(bundle, members)
    except (OSError, tarfile.TarError) as error:
        raise DistributionError("source archive cannot be read") from error
    for source_name, source_content in source_files.items():
        archive_name = f"{prefix}/{source_name}"
        if payloads[archive_name] != source_content:
            raise DistributionError(f"source archive package bytes differ: {archive_name!r}")


def _wheel_file_mode(entry: zipfile.ZipInfo) -> int:
    return entry.external_attr >> 16 if entry.create_system == 3 else 0


def _read_wheel_payloads(
    bundle: zipfile.ZipFile,
    entries: list[zipfile.ZipInfo],
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for entry in entries:
        mode = _wheel_file_mode(entry)
        if entry.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
            raise DistributionError(f"wheel contains a non-regular entry: {entry.filename!r}")
        if entry.flag_bits & 0x1:
            raise DistributionError(f"wheel contains an encrypted entry: {entry.filename!r}")
        if entry.compress_type not in ALLOWED_ZIP_COMPRESSION:
            raise DistributionError(f"wheel uses unsupported compression: {entry.filename!r}")
        if entry.file_size > MAX_MEMBER_BYTES:
            raise DistributionError(f"wheel member is too large: {entry.filename!r}")
        payload = bundle.read(entry)
        if len(payload) != entry.file_size:
            raise DistributionError(f"wheel member size is invalid: {entry.filename!r}")
        payloads[entry.filename] = payload
    return payloads


def _record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return f"sha256={encoded.decode('ascii')}"


def _validate_record(record_path: str, payloads: dict[str, bytes]) -> None:
    try:
        rows = list(csv.reader(io.StringIO(payloads[record_path].decode("utf-8"), newline="")))
    except (UnicodeDecodeError, csv.Error) as error:
        raise DistributionError("wheel RECORD is not valid UTF-8 CSV") from error
    if any(len(row) != 3 for row in rows):
        raise DistributionError("wheel RECORD rows must contain path, digest, and size")
    paths = [row[0] for row in rows]
    _reject_duplicate_names(paths, "wheel RECORD")
    if set(paths) != set(payloads):
        raise DistributionError("wheel RECORD membership does not match the archive")
    for path, digest, size in rows:
        if path == record_path:
            if digest or size:
                raise DistributionError("wheel RECORD must omit its own digest and size")
            continue
        payload = payloads[path]
        if digest != _record_digest(payload) or size != str(len(payload)):
            raise DistributionError(f"wheel RECORD metadata is invalid: {path!r}")


def _check_wheel(archive: Path, source_files: dict[str, bytes]) -> None:
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            names = [entry.filename for entry in entries]
            _reject_duplicate_names(names, "wheel")
            _validate_paths(names)
            files = set(names)
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
            payloads = _read_wheel_payloads(bundle, entries)
            corrupt = bundle.testzip()
            if corrupt is not None:
                raise DistributionError(f"wheel member failed CRC validation: {corrupt!r}")
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise DistributionError("wheel cannot be read") from error
    record_path = f"{metadata}/RECORD"
    _validate_record(record_path, payloads)
    for source_name, source_content in source_files.items():
        archive_name = source_name.removeprefix("src/")
        if payloads[archive_name] != source_content:
            raise DistributionError(f"wheel package bytes differ: {archive_name!r}")


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
