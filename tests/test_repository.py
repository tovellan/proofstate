from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_repository import MAX_TRACKED_BYTES, check_file


def _reject_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Path,
        "open",
        lambda _path, *_args, **_kwargs: pytest.fail("untrusted tracked path was read"),
    )


def test_check_file_rejects_symlink_before_reading_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("external content\n", encoding="utf-8")
    tracked = root / "tracked.txt"
    tracked.symlink_to(outside)
    _reject_reads(monkeypatch)

    assert check_file(root, tracked) == ["tracked.txt: tracked entry must be a regular file"]


def test_check_file_rejects_other_non_regular_entries(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    tracked = root / "tracked.txt"
    tracked.mkdir()

    assert check_file(root, tracked) == ["tracked.txt: tracked entry must be a regular file"]


@pytest.mark.parametrize("path_kind", ["outside", "dotdot"])
def test_check_file_rejects_lexical_boundary_escape_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_kind: str,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("external content\n", encoding="utf-8")
    tracked = outside if path_kind == "outside" else root / ".." / outside.name
    _reject_reads(monkeypatch)

    failures = check_file(root, tracked)

    assert len(failures) == 1
    assert "must stay within the repository root" in failures[0]


def test_check_file_rejects_symlinked_ancestor_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tracked.txt").write_text("external content\n", encoding="utf-8")
    (root / "package").symlink_to(outside, target_is_directory=True)
    tracked = root / "package" / "tracked.txt"
    _reject_reads(monkeypatch)

    assert check_file(root, tracked) == [
        "package/tracked.txt: tracked path ancestor must be an existing directory: 'package'"
    ]


def test_check_file_rejects_symlinked_root_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tracked.txt").write_text("external content\n", encoding="utf-8")
    root = tmp_path / "repository"
    root.symlink_to(outside, target_is_directory=True)
    _reject_reads(monkeypatch)

    assert check_file(root, root / "tracked.txt") == [
        "tracked.txt: tracked path ancestor must be an existing directory: '.'"
    ]


@pytest.mark.parametrize("ancestor_kind", ["missing", "regular"])
def test_check_file_rejects_invalid_ancestor_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ancestor_kind: str,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    ancestor = root / "package"
    if ancestor_kind == "regular":
        ancestor.write_text("not a directory\n", encoding="utf-8")
    tracked = ancestor / "tracked.txt"
    _reject_reads(monkeypatch)

    assert check_file(root, tracked) == [
        "package/tracked.txt: tracked path ancestor must be an existing directory: 'package'"
    ]


def test_check_file_accepts_executable_regular_file(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    tracked = root / "tracked.sh"
    tracked.write_text("exit 0\n", encoding="utf-8")
    tracked.chmod(0o755)

    assert check_file(root, tracked) == []


def test_check_file_normalizes_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    tracked = root / "tracked.txt"
    tracked.write_text("content\n", encoding="utf-8")

    def deny_read(_path: Path, *_args: object, **_kwargs: object) -> None:
        raise PermissionError

    monkeypatch.setattr(Path, "open", deny_read)

    assert check_file(root, tracked) == ["tracked.txt: tracked entry could not be read"]


def test_check_file_rejects_oversize_entry_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    tracked = root / "tracked.txt"
    tracked.touch()
    with tracked.open("r+b") as source:
        source.truncate(MAX_TRACKED_BYTES + 1)
    _reject_reads(monkeypatch)

    assert check_file(root, tracked) == [f"tracked.txt: exceeds {MAX_TRACKED_BYTES} bytes"]
