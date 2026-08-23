"""Read-only Git object access used by the evaluator."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from proofstate.errors import ErrorCode, ProofStateError


@dataclass(frozen=True, slots=True)
class TreeEntry:
    mode: str
    object_type: str
    object_id: str
    path: str


class GitRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def discover(cls, path: Path) -> GitRepository:
        process = cls._execute(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=False,
        )
        if process.returncode != 0:
            raise ProofStateError(
                ErrorCode.NOT_A_GIT_REPOSITORY,
                "repository path is not inside a Git worktree",
            )
        return cls(Path(process.stdout.decode().strip()).resolve())

    @staticmethod
    def _execute(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_LITERAL_PATHSPECS": "1",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "LC_ALL": "C",
            }
        )
        try:
            process = subprocess.run(  # noqa: S603
                args,
                check=False,
                capture_output=True,
                env=environment,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProofStateError(
                ErrorCode.GIT_COMMAND_FAILED,
                "Git command could not be executed",
            ) from error
        if check and process.returncode != 0:
            raise ProofStateError(
                ErrorCode.GIT_COMMAND_FAILED,
                "Git could not read the requested object",
            )
        return process

    def _git(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        return self._execute(["git", "-C", str(self.root), *args], check=check)

    def resolve_commit(self, ref: str) -> str:
        if not ref or len(ref) > 256 or "\x00" in ref:
            raise ProofStateError(ErrorCode.INVALID_ARGUMENT, "invalid Git revision")
        process = self._git(
            ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
            check=False,
        )
        if process.returncode != 0:
            raise ProofStateError(
                ErrorCode.UNRESOLVABLE_COMMIT,
                "Git revision does not resolve to a commit",
            )
        return process.stdout.decode().strip()

    def object_format(self) -> str:
        value = self._git(["rev-parse", "--show-object-format"]).stdout.decode().strip()
        if value not in {"sha1", "sha256"}:
            raise ProofStateError(
                ErrorCode.UNSUPPORTED_OBJECT_FORMAT,
                f"unsupported Git object format: {value}",
            )
        return value

    def tree_id(self, commit: str) -> str:
        return self._git(["rev-parse", f"{commit}^{{tree}}"]).stdout.decode().strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        process = self._git(["merge-base", "--is-ancestor", ancestor, descendant], check=False)
        if process.returncode not in {0, 1}:
            raise ProofStateError(
                ErrorCode.GIT_COMMAND_FAILED,
                "Git could not compare the scorecard and evidence commits",
            )
        return process.returncode == 0

    def entry(self, commit: str, path: str) -> TreeEntry | None:
        process = self._git(["ls-tree", "-z", commit, "--", path])
        if not process.stdout:
            return None
        records = process.stdout.rstrip(b"\x00").split(b"\x00")
        for record in records:
            metadata, separator, raw_path = record.partition(b"\t")
            if not separator:
                continue
            decoded_path = raw_path.decode("utf-8", errors="strict")
            if decoded_path != path:
                continue
            mode, object_type, object_id = metadata.decode().split(" ", maxsplit=2)
            return TreeEntry(mode, object_type, object_id, decoded_path)
        return None

    def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
        entry = self.entry(commit, path)
        if entry is None or entry.object_type != "blob" or entry.mode not in {"100644", "100755"}:
            raise FileNotFoundError(path)
        size = int(self._git(["cat-file", "-s", entry.object_id]).stdout.decode().strip())
        if size > max_bytes:
            raise OverflowError(path)
        return self._git(["cat-file", "blob", entry.object_id]).stdout
