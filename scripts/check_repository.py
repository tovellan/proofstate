"""Validate tracked text, file size, privacy terms, and workflow action pins."""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
from pathlib import Path

MAX_TRACKED_BYTES = 1_048_576
ACTION_USE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*@[0-9a-f]{40}$")
FORBIDDEN_TERMS = (
    "/" + "Users" + "/",
    "startup" + "-idea",
    "tovellan" + "-platform",
    "tovellan" + "-bench",
    "tovellan" + "-trust",
    "tovellan" + "-web",
    "held" + "-out",
)
FORBIDDEN_DASHES = {"\N{EN DASH}", "\N{EM DASH}"}


def _find_git() -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required")
    return git


GIT = _find_git()


def tracked_files(root: Path) -> list[Path]:
    process = subprocess.run(
        [GIT, "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [root / item.decode("utf-8") for item in process.stdout.split(b"\x00") if item]


def check_file(root: Path, path: Path) -> list[str]:
    try:
        relative_path = path.relative_to(root)
    except ValueError:
        return ["tracked path must stay within the repository root"]
    if not relative_path.parts or any(part in {"", ".", ".."} for part in relative_path.parts):
        return [f"{relative_path.as_posix()}: tracked path must stay within the repository root"]

    relative = relative_path.as_posix()
    current = root
    directories = [(current, ".")]
    for part in relative_path.parts[:-1]:
        current /= part
        directories.append((current, current.relative_to(root).as_posix()))
    for directory, label in directories:
        try:
            mode = directory.lstat().st_mode
        except OSError:
            return [f"{relative}: tracked path ancestor must be an existing directory: {label!r}"]
        if not stat.S_ISDIR(mode):
            return [f"{relative}: tracked path ancestor must be an existing directory: {label!r}"]

    current /= relative_path.parts[-1]
    try:
        metadata = current.lstat()
    except OSError:
        return [f"{relative}: tracked entry must be a regular file"]
    if not stat.S_ISREG(metadata.st_mode):
        return [f"{relative}: tracked entry must be a regular file"]
    if metadata.st_size > MAX_TRACKED_BYTES:
        return [f"{relative}: exceeds {MAX_TRACKED_BYTES} bytes"]
    try:
        with current.open("rb") as source:
            content = source.read(MAX_TRACKED_BYTES + 1)
    except OSError:
        return [f"{relative}: tracked entry could not be read"]
    failures: list[str] = []
    if len(content) > MAX_TRACKED_BYTES:
        failures.append(f"{relative}: exceeds {MAX_TRACKED_BYTES} bytes")
    if b"\x00" in content:
        failures.append(f"{relative}: binary content is not allowed")
        return failures
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        failures.append(f"{relative}: tracked text must be valid UTF-8")
        return failures
    failures.extend(
        f"{relative}: contains a prohibited Unicode dash"
        for dash in FORBIDDEN_DASHES
        if dash in text
    )
    haystacks = (relative, text)
    failures.extend(
        f"{relative}: contains a private-boundary term"
        for term in FORBIDDEN_TERMS
        if any(term.casefold() in value.casefold() for value in haystacks)
    )
    if relative.startswith(".github/workflows/"):
        for action in ACTION_USE.findall(text):
            if action.startswith("./"):
                continue
            if not PINNED_ACTION.fullmatch(action):
                failures.append(f"{relative}: action is not pinned to a 40-character commit")
    return failures


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    failures = [failure for path in tracked_files(root) for failure in check_file(root, path)]
    if failures:
        for failure in sorted(failures):
            print(failure)
        raise SystemExit(1)
    print("tracked repository review passed")


if __name__ == "__main__":
    main()
