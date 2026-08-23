from __future__ import annotations

import base64
import csv
import hashlib
import io
import stat
import sys
import tarfile
import warnings
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

import scripts.check_distribution as distribution
from scripts.check_distribution import (
    MAX_MEMBER_BYTES,
    DistributionError,
    _check_sdist,
    _check_wheel,
    _source_files,
    _validate_paths,
)

SOURCE_PATH = "src/proofstate/__init__.py"
WHEEL_SOURCE_PATH = "proofstate/__init__.py"
SOURCE_BYTES = b'__version__ = "0.0.0"\n'
SOURCE_FILES = {SOURCE_PATH: SOURCE_BYTES}
SDIST_PREFIX = "proofstate-0.0.0"
DIST_INFO = "proofstate-0.0.0.dist-info"
RECORD_PATH = f"{DIST_INFO}/RECORD"


def _digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return f"sha256={encoded.decode('ascii')}"


def _record(
    payloads: Mapping[str, bytes],
    *,
    omitted: frozenset[str] = frozenset(),
    digest_overrides: Mapping[str, str] | None = None,
    size_overrides: Mapping[str, int] | None = None,
) -> bytes:
    digest_overrides = digest_overrides or {}
    size_overrides = size_overrides or {}
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, payload in sorted(payloads.items()):
        if name not in omitted:
            writer.writerow(
                [
                    name,
                    digest_overrides.get(name, _digest(payload)),
                    size_overrides.get(name, len(payload)),
                ]
            )
    writer.writerow([RECORD_PATH, "", ""])
    return output.getvalue().encode("utf-8")


def _wheel_payloads(
    *,
    source_bytes: bytes = SOURCE_BYTES,
    record_omitted: frozenset[str] = frozenset(),
    record_digest_overrides: Mapping[str, str] | None = None,
    record_size_overrides: Mapping[str, int] | None = None,
) -> dict[str, bytes]:
    payloads = {
        WHEEL_SOURCE_PATH: source_bytes,
        f"{DIST_INFO}/METADATA": (b"Metadata-Version: 2.4\nName: proofstate\nVersion: 0.0.0\n"),
        f"{DIST_INFO}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: distribution-test\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{DIST_INFO}/entry_points.txt": b"[console_scripts]\nproofstate=proofstate.cli:main\n",
        f"{DIST_INFO}/licenses/LICENSE": b"Test license\n",
    }
    payloads[RECORD_PATH] = _record(
        payloads,
        omitted=record_omitted,
        digest_overrides=record_digest_overrides,
        size_overrides=record_size_overrides,
    )
    return payloads


def _zip_info(name: str, mode: int, compression: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2025, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = mode << 16
    info.compress_type = compression
    return info


def _write_wheel(
    path: Path,
    *,
    payloads: Mapping[str, bytes] | None = None,
    mode_overrides: Mapping[str, int] | None = None,
    compression_overrides: Mapping[str, int] | None = None,
    duplicate: str | None = None,
    directory: str | None = None,
) -> None:
    payloads = payloads or _wheel_payloads()
    mode_overrides = mode_overrides or {}
    compression_overrides = compression_overrides or {}
    with zipfile.ZipFile(path, mode="w") as archive:
        for name, payload in payloads.items():
            info = _zip_info(
                name,
                mode_overrides.get(name, stat.S_IFREG | 0o644),
                compression_overrides.get(name, zipfile.ZIP_STORED),
            )
            archive.writestr(info, payload)
        if duplicate is not None:
            info = _zip_info(duplicate, stat.S_IFREG | 0o644, zipfile.ZIP_STORED)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(info, payloads[duplicate])
        if directory is not None:
            info = _zip_info(directory, stat.S_IFDIR | 0o755, zipfile.ZIP_STORED)
            archive.writestr(info, b"")


def _sdist_payloads(*, source_bytes: bytes = SOURCE_BYTES) -> dict[str, bytes]:
    return {
        ".gitignore": b"dist/\n",
        "LICENSE": b"Test license\n",
        "PKG-INFO": b"Metadata-Version: 2.4\nName: proofstate\nVersion: 0.0.0\n",
        "README.md": b"# ProofState\n",
        "pyproject.toml": b'[project]\nname = "proofstate"\nversion = "0.0.0"\n',
        SOURCE_PATH: source_bytes,
    }


def _write_sdist(
    path: Path,
    *,
    payloads: Mapping[str, bytes] | None = None,
    type_overrides: Mapping[str, bytes] | None = None,
    duplicate: str | None = None,
) -> None:
    payloads = payloads or _sdist_payloads()
    type_overrides = type_overrides or {}
    with tarfile.open(path, mode="w:gz") as archive:
        for relative_name, payload in payloads.items():
            info = tarfile.TarInfo(f"{SDIST_PREFIX}/{relative_name}")
            info.mode = 0o644
            info.type = type_overrides.get(relative_name, tarfile.REGTYPE)
            if info.isreg():
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            else:
                if info.issym():
                    info.linkname = "target"
                archive.addfile(info)
        if duplicate is not None:
            payload = payloads[duplicate]
            info = tarfile.TarInfo(f"{SDIST_PREFIX}/{duplicate}")
            info.mode = 0o644
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _mark_zip_entries_encrypted(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        local_offsets = [entry.header_offset for entry in archive.infolist()]

    contents = bytearray(path.read_bytes())
    for offset in local_offsets:
        assert contents[offset : offset + 4] == b"PK\x03\x04"
        flags = int.from_bytes(contents[offset + 6 : offset + 8], "little") | 1
        contents[offset + 6 : offset + 8] = flags.to_bytes(2, "little")

    offset = 0
    central_headers = 0
    while (offset := contents.find(b"PK\x01\x02", offset)) != -1:
        flags = int.from_bytes(contents[offset + 8 : offset + 10], "little") | 1
        contents[offset + 8 : offset + 10] = flags.to_bytes(2, "little")
        central_headers += 1
        offset += 4
    assert central_headers == len(local_offsets)
    path.write_bytes(contents)


def _corrupt_stored_member(path: Path, name: str) -> None:
    with zipfile.ZipFile(path) as archive:
        entry = archive.getinfo(name)
    assert entry.compress_type == zipfile.ZIP_STORED
    contents = bytearray(path.read_bytes())
    offset = entry.header_offset
    name_length = int.from_bytes(contents[offset + 26 : offset + 28], "little")
    extra_length = int.from_bytes(contents[offset + 28 : offset + 30], "little")
    payload_offset = offset + 30 + name_length + extra_length
    contents[payload_offset] ^= 1
    path.write_bytes(contents)


def test_matching_archives_pass(tmp_path: Path) -> None:
    wheel = tmp_path / "proofstate-0.0.0-py3-none-any.whl"
    sdist = tmp_path / "proofstate-0.0.0.tar.gz"
    _write_wheel(wheel)
    _write_sdist(sdist)

    _check_wheel(wheel, SOURCE_FILES)
    _check_sdist(sdist, SOURCE_FILES)


def test_source_files_include_yaml_fixtures(tmp_path: Path) -> None:
    package = tmp_path / "src" / "proofstate"
    fixtures = package / "fixtures"
    fixtures.mkdir(parents=True)
    (package / "module.py").write_text("value = 1\n", encoding="utf-8")
    (package / "module.py").chmod(0o755)
    (fixtures / "case.json").write_text("{}\n", encoding="utf-8")
    (fixtures / "case.yaml").write_text("value: true\n", encoding="utf-8")
    (fixtures / "ignored.txt").write_text("not packaged\n", encoding="utf-8")

    assert set(_source_files(tmp_path)) == {
        "src/proofstate/fixtures/case.json",
        "src/proofstate/fixtures/case.yaml",
        "src/proofstate/module.py",
    }


@pytest.mark.parametrize("symlink_component", ["root", "src", "package"])
def test_source_files_reject_symlinked_directory_chain_before_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    symlink_component: str,
) -> None:
    root = tmp_path / "repository"
    external = tmp_path / f"external-{symlink_component}"
    if symlink_component == "root":
        (external / "src" / "proofstate").mkdir(parents=True)
        root.symlink_to(external, target_is_directory=True)
    elif symlink_component == "src":
        root.mkdir()
        (external / "proofstate").mkdir(parents=True)
        (root / "src").symlink_to(external, target_is_directory=True)
    else:
        (root / "src").mkdir(parents=True)
        external.mkdir()
        (root / "src" / "proofstate").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(
        Path,
        "iterdir",
        lambda _path: pytest.fail("untrusted source directory was traversed"),
    )

    with pytest.raises(DistributionError, match="source package directory"):
        _source_files(root)


def test_source_files_reject_symlinks_without_reading_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "src" / "proofstate"
    package.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("external = True\n", encoding="utf-8")
    (package / "module.py").symlink_to(outside)
    monkeypatch.setattr(
        Path,
        "open",
        lambda _path, *_args, **_kwargs: pytest.fail("source symlink was read"),
    )

    with pytest.raises(DistributionError, match="non-regular entry"):
        _source_files(tmp_path)


def test_source_files_reject_internal_directory_symlink_before_build_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "src" / "proofstate"
    package.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.py").write_text("external = True\n", encoding="utf-8")
    (package / "fixtures").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        Path,
        "open",
        lambda _path, *_args, **_kwargs: pytest.fail("source directory symlink target was read"),
    )

    with pytest.raises(DistributionError, match="non-regular entry"):
        _source_files(tmp_path)


def test_source_files_fail_closed_when_nested_directory_cannot_be_enumerated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "src" / "proofstate"
    hidden = package / "hidden"
    hidden.mkdir(parents=True)
    (hidden / "module.py").write_text("value = 1\n", encoding="utf-8")
    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path) -> Iterator[Path]:
        if path == hidden:
            raise PermissionError
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)

    with pytest.raises(DistributionError, match="directory could not be read"):
        _source_files(tmp_path)


def test_source_files_reject_matching_non_regular_entries(tmp_path: Path) -> None:
    package = tmp_path / "src" / "proofstate"
    package.mkdir(parents=True)
    (package / "module.py").mkdir()

    with pytest.raises(DistributionError, match="non-regular entry"):
        _source_files(tmp_path)


def test_source_files_normalize_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "src" / "proofstate"
    package.mkdir(parents=True)
    (package / "module.py").write_text("value = 1\n", encoding="utf-8")

    def deny_read(_path: Path, *_args: object, **_kwargs: object) -> None:
        raise PermissionError

    monkeypatch.setattr(Path, "open", deny_read)

    with pytest.raises(DistributionError, match="could not be read"):
        _source_files(tmp_path)


@pytest.mark.parametrize("filename", ["module.py", "payload.txt"])
def test_source_files_reject_oversize_entry_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    package = tmp_path / "src" / "proofstate"
    package.mkdir(parents=True)
    source_path = package / filename
    source_path.touch()
    with source_path.open("r+b") as source:
        source.truncate(MAX_MEMBER_BYTES + 1)
    monkeypatch.setattr(
        Path,
        "open",
        lambda _path, *_args, **_kwargs: pytest.fail("oversize source entry was read"),
    )

    with pytest.raises(DistributionError, match="entry is too large"):
        _source_files(tmp_path)


def test_wheel_rejects_duplicate_members(tmp_path: Path) -> None:
    wheel = tmp_path / "duplicate.whl"
    _write_wheel(wheel, duplicate=WHEEL_SOURCE_PATH)

    with pytest.raises(DistributionError):
        _check_wheel(wheel, SOURCE_FILES)


@pytest.mark.parametrize("member_mode", [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600])
def test_wheel_rejects_symlinks_and_special_files(tmp_path: Path, member_mode: int) -> None:
    wheel = tmp_path / "special.whl"
    _write_wheel(wheel, mode_overrides={WHEEL_SOURCE_PATH: member_mode})

    with pytest.raises(DistributionError):
        _check_wheel(wheel, SOURCE_FILES)


def test_wheel_rejects_directory_members(tmp_path: Path) -> None:
    wheel = tmp_path / "directory.whl"
    _write_wheel(wheel, directory="proofstate/")

    with pytest.raises(DistributionError):
        _check_wheel(wheel, SOURCE_FILES)


def test_wheel_rejects_encrypted_members(tmp_path: Path) -> None:
    wheel = tmp_path / "encrypted.whl"
    _write_wheel(wheel)
    _mark_zip_entries_encrypted(wheel)

    with pytest.raises(DistributionError):
        _check_wheel(wheel, SOURCE_FILES)


def test_wheel_rejects_unsupported_compression(tmp_path: Path) -> None:
    wheel = tmp_path / "bzip2.whl"
    _write_wheel(
        wheel,
        compression_overrides={WHEEL_SOURCE_PATH: zipfile.ZIP_BZIP2},
    )

    with pytest.raises(DistributionError):
        _check_wheel(wheel, SOURCE_FILES)


@pytest.mark.parametrize("record_defect", ["membership", "digest", "size"])
def test_wheel_rejects_invalid_record(tmp_path: Path, record_defect: str) -> None:
    wheel = tmp_path / f"record-{record_defect}.whl"
    if record_defect == "membership":
        payloads = _wheel_payloads(record_omitted=frozenset({WHEEL_SOURCE_PATH}))
    elif record_defect == "digest":
        payloads = _wheel_payloads(record_digest_overrides={WHEEL_SOURCE_PATH: "sha256=AAAA"})
    else:
        payloads = _wheel_payloads(record_size_overrides={WHEEL_SOURCE_PATH: len(SOURCE_BYTES) + 1})
    _write_wheel(wheel, payloads=payloads)

    with pytest.raises(DistributionError):
        _check_wheel(wheel, SOURCE_FILES)


def test_wheel_rejects_crc_corruption(tmp_path: Path) -> None:
    wheel = tmp_path / "corrupt.whl"
    _write_wheel(wheel)
    _corrupt_stored_member(wheel, f"{DIST_INFO}/METADATA")

    with pytest.raises(DistributionError):
        _check_wheel(wheel, SOURCE_FILES)


def test_wheel_rejects_source_byte_mismatch(tmp_path: Path) -> None:
    wheel = tmp_path / "source-mismatch.whl"
    _write_wheel(wheel, payloads=_wheel_payloads(source_bytes=b"tampered = True\n"))

    with pytest.raises(DistributionError):
        _check_wheel(wheel, SOURCE_FILES)


def test_sdist_rejects_duplicate_members(tmp_path: Path) -> None:
    sdist = tmp_path / "duplicate.tar.gz"
    _write_sdist(sdist, duplicate=SOURCE_PATH)

    with pytest.raises(DistributionError):
        _check_sdist(sdist, SOURCE_FILES)


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.FIFOTYPE])
def test_sdist_rejects_links_and_special_files(tmp_path: Path, member_type: bytes) -> None:
    sdist = tmp_path / "special.tar.gz"
    _write_sdist(sdist, type_overrides={SOURCE_PATH: member_type})

    with pytest.raises(DistributionError):
        _check_sdist(sdist, SOURCE_FILES)


def test_sdist_rejects_source_byte_mismatch(tmp_path: Path) -> None:
    sdist = tmp_path / "source-mismatch.tar.gz"
    _write_sdist(sdist, payloads=_sdist_payloads(source_bytes=b"tampered = True\n"))

    with pytest.raises(DistributionError):
        _check_sdist(sdist, SOURCE_FILES)


@pytest.mark.parametrize("name", ["/absolute.py", "../escape.py", "proofstate/./hidden.py"])
def test_archive_paths_reject_unsafe_and_dot_segments(name: str) -> None:
    with pytest.raises(DistributionError):
        _validate_paths({name})


def test_source_only_preflight_does_not_inspect_distribution_archives(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(distribution, "_source_files", lambda _root: {})
    monkeypatch.setattr(
        distribution,
        "_single",
        lambda _directory, _pattern: pytest.fail("archive lookup must not run"),
    )
    monkeypatch.setattr(sys, "argv", ["check_distribution.py", "--source-only"])

    distribution.main()

    assert capsys.readouterr().out == "package source preflight passed\n"
