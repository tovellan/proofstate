"""Validate tracked text, file size, privacy terms, and workflow action pins."""

from __future__ import annotations

import re
import shutil
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
GIT = shutil.which("git")
if GIT is None:
    raise RuntimeError("git is required")


def tracked_files(root: Path) -> list[Path]:
    process = subprocess.run(
        [GIT, "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [root / item.decode("utf-8") for item in process.stdout.split(b"\x00") if item]


def check_file(root: Path, path: Path) -> list[str]:
    relative = path.relative_to(root).as_posix()
    failures: list[str] = []
    content = path.read_bytes()
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
