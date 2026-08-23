"""Read-only Git object access used by the evaluator."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from proofstate.errors import ErrorCode, ProofStateError

ENTRY_PREFETCH_MAX_PATHS = 256
ENTRY_PREFETCH_MAX_PATH_BYTES = 16_384
ENTRY_PREFETCH_MAX_CHUNKS = 256


class _EntryLookupFailed:
    pass


_ENTRY_LOOKUP_FAILED = _EntryLookupFailed()


class GitLookupLimitError(RuntimeError):
    pass


class _EntryLookupLimited:
    pass


_ENTRY_LOOKUP_LIMITED = _EntryLookupLimited()


@dataclass(frozen=True, slots=True)
class TreeEntry:
    mode: str
    object_type: str
    object_id: str
    path: str
    size: int | None = None


class GitRepository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._entry_cache: dict[
            tuple[str, str], TreeEntry | _EntryLookupFailed | _EntryLookupLimited | None
        ] = {}
        self._entry_prefetch_chunks = 0

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
        if not process.stdout.endswith(b"\n") or len(process.stdout) == 1:
            raise ProofStateError(
                ErrorCode.GIT_COMMAND_FAILED,
                "Git returned an invalid repository root",
            )
        try:
            raw_root = Path(os.fsdecode(process.stdout[:-1]))
            if not raw_root.is_absolute():
                raise ValueError("repository root is not absolute")
            root = raw_root.resolve()
        except (OSError, UnicodeError, ValueError) as error:
            raise ProofStateError(
                ErrorCode.GIT_COMMAND_FAILED,
                "Git returned an invalid repository root",
            ) from error
        return cls(root)

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
        except (OSError, UnicodeError, subprocess.TimeoutExpired) as error:
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
        cache_key = (commit, path)
        if cache_key not in self._entry_cache:
            self.prefetch_entries(commit, [path])
        cached = self._entry_cache[cache_key]
        if cached is _ENTRY_LOOKUP_FAILED:
            raise ProofStateError(
                ErrorCode.GIT_COMMAND_FAILED,
                "Git could not read the requested tree entries",
            )
        if cached is _ENTRY_LOOKUP_LIMITED:
            raise GitLookupLimitError("Git tree lookup work limit is exhausted")
        assert cached is None or isinstance(cached, TreeEntry)
        return cached

    def prefetch_entries(self, commit: str, paths: list[str]) -> None:
        unseen: list[str] = []
        seen: set[str] = set()
        for path in paths:
            cache_key = (commit, path)
            if cache_key in self._entry_cache or path in seen:
                continue
            seen.add(path)
            unseen.append(path)

        chunk: list[str] = []
        chunk_bytes = 0
        for path in unseen:
            path_bytes = len(path.encode("utf-8", errors="surrogatepass")) + 1
            if path_bytes > ENTRY_PREFETCH_MAX_PATH_BYTES:
                if chunk:
                    self._prefetch_entry_chunk(commit, chunk)
                    chunk = []
                    chunk_bytes = 0
                self._entry_cache[(commit, path)] = _ENTRY_LOOKUP_LIMITED
                continue
            if chunk and (
                len(chunk) >= ENTRY_PREFETCH_MAX_PATHS
                or chunk_bytes + path_bytes > ENTRY_PREFETCH_MAX_PATH_BYTES
                or any(
                    path.startswith(f"{other}/") or other.startswith(f"{path}/") for other in chunk
                )
            ):
                self._prefetch_entry_chunk(commit, chunk)
                chunk = []
                chunk_bytes = 0
            chunk.append(path)
            chunk_bytes += path_bytes
        if chunk:
            self._prefetch_entry_chunk(commit, chunk)

    def _prefetch_entry_chunk(self, commit: str, paths: list[str]) -> None:
        cache_keys = [(commit, path) for path in paths]
        if self._entry_prefetch_chunks >= ENTRY_PREFETCH_MAX_CHUNKS:
            for cache_key in cache_keys:
                self._entry_cache[cache_key] = _ENTRY_LOOKUP_LIMITED
            return
        self._entry_prefetch_chunks += 1
        try:
            process = self._git(["ls-tree", "-l", "-z", commit, "--", *paths])
            requested = {path.encode("utf-8", errors="surrogatepass"): path for path in paths}
            found: dict[str, TreeEntry] = {}
            records = process.stdout.split(b"\x00")
            if records[-1]:
                raise ValueError("unterminated Git tree output")
            if len(records) - 1 > len(paths):
                raise ValueError("Git tree output exceeds request cardinality")
            for record in records[:-1]:
                metadata, separator, raw_path = record.partition(b"\t")
                if not separator:
                    raise ValueError("malformed Git tree output")
                decoded_path = requested.get(raw_path)
                if decoded_path is None:
                    continue
                if decoded_path in found:
                    raise ValueError("unexpected Git tree output")
                parts = metadata.decode("ascii").split()
                if len(parts) != 4:
                    raise ValueError("malformed Git tree metadata")
                mode, object_type, object_id, raw_size = parts
                if raw_size == "-":
                    size = None
                elif raw_size.isascii() and raw_size.isdecimal():
                    size = int(raw_size)
                else:
                    raise ValueError("malformed Git tree size")
                if object_type == "blob" and size is None:
                    raise ValueError("Git blob size is missing")
                found[decoded_path] = TreeEntry(
                    mode,
                    object_type,
                    object_id,
                    decoded_path,
                    size,
                )
        except (ProofStateError, UnicodeError, ValueError):
            for cache_key in cache_keys:
                self._entry_cache[cache_key] = _ENTRY_LOOKUP_FAILED
            return
        for path, cache_key in zip(paths, cache_keys, strict=True):
            self._entry_cache[cache_key] = found.get(path)

    def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
        entry = self.entry(commit, path)
        if entry is None or entry.object_type != "blob" or entry.mode not in {"100644", "100755"}:
            raise FileNotFoundError(path)
        if entry.size is None:
            raise ProofStateError(
                ErrorCode.GIT_COMMAND_FAILED,
                "Git did not report the requested blob size",
            )
        size = entry.size
        if size > max_bytes:
            raise OverflowError(path)
        return self._git(["cat-file", "blob", entry.object_id]).stdout
