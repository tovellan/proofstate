"""Evidence verification against immutable Git objects."""

from __future__ import annotations

import ast
import hashlib
import re
from bisect import bisect_left
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import ValidationError

from proofstate.document import (
    MAX_DOCUMENT_NODES,
    DocumentError,
    count_document_nodes,
    load_document,
)
from proofstate.errors import ErrorCode, ProofStateError
from proofstate.git import GitLookupLimitError, GitRepository
from proofstate.models import (
    ArtifactCheck,
    ArtifactEvidence,
    ArtifactOperator,
    AttestationEvidence,
    FileEvidence,
    HumanAttestation,
    MachineEvidence,
    TestSymbolEvidence,
)

TEST_SOURCE_MAX_BYTES = 65_536
EVALUATION_MAX_EVIDENCE_INPUT_BYTES = 10_485_760
EVALUATION_MAX_EVIDENCE_SOURCES = 256
ARTIFACT_CACHE_MAX_INPUT_BYTES = 10_485_760
ARTIFACT_CACHE_MAX_NODES = MAX_DOCUMENT_NODES * 8
ATTESTATION_CACHE_MAX_INPUT_BYTES = 10_485_760
ATTESTATION_CACHE_MAX_NODES = MAX_DOCUMENT_NODES * 8
STRUCTURAL_DIGEST_MEMO_MIN_ITEMS = 64


class EvidenceCode(StrEnum):
    VERIFIED = "PSE000_VERIFIED"
    DEPENDENCY_FAILED = "PSE001_DEPENDENCY_FAILED"
    FILE_MISSING = "PSE101_FILE_MISSING"
    FILE_TOO_LARGE = "PSE102_FILE_TOO_LARGE"
    DIGEST_MISMATCH = "PSE103_DIGEST_MISMATCH"
    EVALUATION_LIMIT = "PSE104_EVALUATION_LIMIT"
    TEST_PARSE_FAILED = "PSE201_TEST_PARSE_FAILED"
    TEST_SYMBOL_MISSING = "PSE202_TEST_SYMBOL_MISSING"
    ARTIFACT_INVALID = "PSE301_ARTIFACT_INVALID"
    ARTIFACT_CHECK_FAILED = "PSE302_ARTIFACT_CHECK_FAILED"
    ATTESTATION_INVALID = "PSE401_ATTESTATION_INVALID"
    ATTESTATION_NOT_YET_VALID = "PSE402_ATTESTATION_NOT_YET_VALID"
    ATTESTATION_EXPIRED = "PSE403_ATTESTATION_EXPIRED"
    ATTESTATION_SCOPE_MISMATCH = "PSE404_ATTESTATION_SCOPE_MISMATCH"
    INTERNAL_ERROR = "PSE900_INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    evidence_type: str
    passed: bool
    code: EvidenceCode
    message: str
    path: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.evidence_type,
            "passed": self.passed,
            "code": self.code.value,
            "message": self.message,
        }
        if self.path is not None:
            result["path"] = self.path
        if self.details:
            result["details"] = deepcopy(self.details)
        return result


_TestSymbolCacheKey = tuple[GitRepository, str, str, int]
_TestSymbolKey = tuple[str, ...]
_TestSymbolCache = dict[_TestSymbolCacheKey, set[_TestSymbolKey] | EvidenceResult]
_FileDigestCache = dict[tuple[GitRepository, str, str, int], str | EvidenceResult]
_ArtifactCacheKey = tuple[GitRepository, str, str, int, str]
_AttestationCacheKey = tuple[GitRepository, str, str, int]


class _EvaluationLimitError(OverflowError):
    pass


@dataclass(slots=True)
class _EvaluationWorkBudget:
    input_bytes: int = 0
    sources: int = 0
    exhausted: bool = False

    def reserve(
        self,
        repository: GitRepository,
        commit: str,
        path: str,
        max_bytes: int,
    ) -> None:
        entry = repository.entry(commit, path)
        if entry is None or entry.object_type != "blob" or entry.mode not in {"100644", "100755"}:
            raise FileNotFoundError(path)
        if entry.size is None:
            raise ProofStateError(
                ErrorCode.GIT_COMMAND_FAILED,
                "Git did not report the requested blob size",
            )
        if entry.size > max_bytes:
            raise OverflowError(path)
        if (
            self.exhausted
            or self.sources >= EVALUATION_MAX_EVIDENCE_SOURCES
            or self.input_bytes + entry.size > EVALUATION_MAX_EVIDENCE_INPUT_BYTES
        ):
            self.exhausted = True
            raise _EvaluationLimitError(path)
        self.sources += 1
        self.input_bytes += entry.size


@dataclass(frozen=True, slots=True)
class _ArtifactMaterial:
    digest: str
    parsed: bool = False
    valid: bool = False
    document: Any = None
    input_bytes: int = 0
    node_count: int = 0
    resource_limited: bool = False
    structural_digests: dict[int, tuple[Any, bytes]] = field(
        default_factory=dict,
        compare=False,
    )


@dataclass(slots=True)
class _ArtifactCache:
    values: dict[_ArtifactCacheKey, _ArtifactMaterial | EvidenceResult] = field(
        default_factory=dict
    )
    retained_input_bytes: int = 0
    retained_nodes: int = 0
    retained_documents: int = 0
    budget_exhausted: bool = False
    retained_order: OrderedDict[_ArtifactCacheKey, None] = field(default_factory=OrderedDict)
    scalar_contains: dict[
        int,
        tuple[list[Any], _ListMembershipIndex],
    ] = field(default_factory=dict)
    scalar_owners: dict[_ArtifactCacheKey, set[int]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _AttestationMaterial:
    digest: str
    parsed: bool = False
    attestation: HumanAttestation | None = None
    input_bytes: int = 0
    node_count: int = 0
    resource_limited: bool = False


@dataclass(slots=True)
class _AttestationCache:
    values: dict[_AttestationCacheKey, _AttestationMaterial | EvidenceResult] = field(
        default_factory=dict
    )
    retained_input_bytes: int = 0
    retained_nodes: int = 0
    retained_documents: int = 0
    budget_exhausted: bool = False
    retained_order: OrderedDict[_AttestationCacheKey, None] = field(default_factory=OrderedDict)


@dataclass(frozen=True, slots=True)
class _ScalarMembershipIndex:
    has_null: bool
    booleans: tuple[bool, ...]
    integers: tuple[int, ...]
    floats: tuple[float, ...]
    strings: tuple[str, ...]

    def contains(self, value: Any) -> bool:
        value_type = type(value)
        if value is None:
            return self.has_null
        if value_type is bool:
            return value in self.booleans
        if value_type is int:
            index = bisect_left(self.integers, value)
            return index < len(self.integers) and self.integers[index] == value
        if value_type is float:
            index = bisect_left(self.floats, value)
            return index < len(self.floats) and self.floats[index] == value
        if value_type is str:
            index = bisect_left(self.strings, value)
            return index < len(self.strings) and self.strings[index] == value
        return False


@dataclass(frozen=True, slots=True)
class _ListMembershipIndex:
    scalars: _ScalarMembershipIndex
    composite_digests: tuple[bytes, ...]
    composite_values: tuple[tuple[Any, ...], ...]

    def contains(
        self,
        value: Any,
        *,
        structural_digest_memo: dict[int, tuple[Any, bytes]] | None = None,
    ) -> bool:
        if type(value) in {list, dict}:
            digest = _json_value_digest(value, memo=structural_digest_memo)
            index = bisect_left(self.composite_digests, digest)
            if index == len(self.composite_digests) or self.composite_digests[index] != digest:
                return False
            candidates = self.composite_values[index]
            return any(_json_values_equal(candidate, value) for candidate in candidates)
        return self.scalars.contains(value)


def _touch_retained_key(keys: OrderedDict[Any, None], key: Any) -> None:
    keys.move_to_end(key)


def _retain_artifact_material(
    cache: _ArtifactCache,
    key: _ArtifactCacheKey,
    material: _ArtifactMaterial,
) -> bool:
    if (
        material.input_bytes > ARTIFACT_CACHE_MAX_INPUT_BYTES
        or material.node_count > ARTIFACT_CACHE_MAX_NODES
        or cache.retained_input_bytes + material.input_bytes > ARTIFACT_CACHE_MAX_INPUT_BYTES
        or cache.retained_nodes + material.node_count > ARTIFACT_CACHE_MAX_NODES
    ):
        cache.budget_exhausted = True
        cache.values[key] = _ArtifactMaterial(
            material.digest,
            parsed=True,
            resource_limited=True,
        )
        return False
    cache.values[key] = material
    cache.retained_documents += 1
    cache.retained_input_bytes += material.input_bytes
    cache.retained_nodes += material.node_count
    cache.retained_order[key] = None
    return True


def _retain_attestation_material(
    cache: _AttestationCache,
    key: _AttestationCacheKey,
    material: _AttestationMaterial,
) -> bool:
    if (
        material.input_bytes > ATTESTATION_CACHE_MAX_INPUT_BYTES
        or material.node_count > ATTESTATION_CACHE_MAX_NODES
        or cache.retained_input_bytes + material.input_bytes > ATTESTATION_CACHE_MAX_INPUT_BYTES
        or cache.retained_nodes + material.node_count > ATTESTATION_CACHE_MAX_NODES
    ):
        cache.budget_exhausted = True
        cache.values[key] = _AttestationMaterial(
            material.digest,
            parsed=True,
            resource_limited=True,
        )
        return False
    cache.values[key] = material
    cache.retained_documents += 1
    cache.retained_input_bytes += material.input_bytes
    cache.retained_nodes += material.node_count
    cache.retained_order[key] = None
    return True


def _cache_test_symbols(
    cache: _TestSymbolCache,
    key: _TestSymbolCacheKey,
    value: set[_TestSymbolKey] | EvidenceResult,
) -> None:
    cache[key] = value


def _digest_matches(content: bytes, expected: str | None) -> bool:
    return expected is None or hashlib.sha256(content).hexdigest() == expected


def _read_evidence_blob(
    repository: GitRepository,
    commit: str,
    path: str,
    max_bytes: int,
    evidence_type: str,
    work_budget: _EvaluationWorkBudget | None = None,
) -> tuple[bytes | None, EvidenceResult | None]:
    try:
        if work_budget is not None:
            work_budget.reserve(repository, commit, path, max_bytes)
        return repository.read_blob(commit, path, max_bytes=max_bytes), None
    except GitLookupLimitError:
        return None, EvidenceResult(
            evidence_type,
            False,
            EvidenceCode.EVALUATION_LIMIT,
            "evaluation exceeds the cumulative Git tree lookup budget",
            path,
        )
    except _EvaluationLimitError:
        return None, EvidenceResult(
            evidence_type,
            False,
            EvidenceCode.EVALUATION_LIMIT,
            "evaluation exceeds the cumulative evidence input budget",
            path,
        )
    except FileNotFoundError:
        return None, EvidenceResult(
            evidence_type,
            False,
            EvidenceCode.FILE_MISSING,
            "evidence is absent or is not a regular file in the pinned tree",
            path,
        )
    except OverflowError:
        return None, EvidenceResult(
            evidence_type,
            False,
            EvidenceCode.FILE_TOO_LARGE,
            "evidence exceeds the applicable byte limit",
            path,
        )


def verify_file(
    evidence: FileEvidence,
    repository: GitRepository,
    commit: str,
    max_bytes: int,
    *,
    cache: _FileDigestCache | None = None,
    work_budget: _EvaluationWorkBudget | None = None,
) -> EvidenceResult:
    cache_key = (repository, commit, evidence.path, max_bytes)
    cached = cache.get(cache_key) if cache is not None else None
    if cached is None:
        try:
            content, error = _read_evidence_blob(
                repository,
                commit,
                evidence.path,
                max_bytes,
                evidence.type,
                work_budget,
            )
        except ProofStateError:
            internal_error = EvidenceResult(
                evidence.type,
                False,
                EvidenceCode.INTERNAL_ERROR,
                "Git object verification failed closed",
                evidence.path,
            )
            if cache is not None:
                cache[cache_key] = internal_error
            return deepcopy(internal_error)
        if error is not None:
            if cache is not None:
                cache[cache_key] = error
            return deepcopy(error)
        assert content is not None
        digest = hashlib.sha256(content).hexdigest()
        if cache is not None:
            cache[cache_key] = digest
    elif isinstance(cached, EvidenceResult):
        return deepcopy(cached)
    else:
        digest = cached
    if evidence.sha256 is not None and digest != evidence.sha256:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.DIGEST_MISMATCH,
            "file digest does not match the scorecard",
            evidence.path,
        )
    return EvidenceResult(
        evidence.type,
        True,
        EvidenceCode.VERIFIED,
        "regular file exists in the pinned tree",
        evidence.path,
        {"sha256": digest},
    )


def _collect_pytest_symbols(tree: ast.Module) -> set[_TestSymbolKey]:
    symbols: set[_TestSymbolKey] = set()
    function_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, function_types) and node.name.startswith("test_"):
            symbols.add((node.name,))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for child in node.body:
                if isinstance(child, function_types) and child.name.startswith("test_"):
                    symbols.add((node.name, child.name))
    return symbols


def verify_test_symbol(
    evidence: TestSymbolEvidence,
    repository: GitRepository,
    commit: str,
    max_bytes: int,
    *,
    cache: _TestSymbolCache | None = None,
    work_budget: _EvaluationWorkBudget | None = None,
) -> EvidenceResult:
    effective_max_bytes = min(max_bytes, TEST_SOURCE_MAX_BYTES)
    cache_key = (repository, commit, evidence.path, effective_max_bytes)
    cached = cache.get(cache_key) if cache is not None else None
    if cached is None:
        try:
            content, error = _read_evidence_blob(
                repository,
                commit,
                evidence.path,
                effective_max_bytes,
                evidence.type,
                work_budget,
            )
        except ProofStateError:
            internal_error = EvidenceResult(
                evidence.type,
                False,
                EvidenceCode.INTERNAL_ERROR,
                "Git object verification failed closed",
                evidence.path,
            )
            if cache is not None:
                _cache_test_symbols(cache, cache_key, internal_error)
            return deepcopy(internal_error)
        if error is not None:
            if cache is not None:
                _cache_test_symbols(cache, cache_key, error)
            return deepcopy(error)
        assert content is not None
        try:
            tree = ast.parse(content, filename=evidence.path)
        except (SyntaxError, ValueError, TypeError, RecursionError):
            parse_error = EvidenceResult(
                evidence.type,
                False,
                EvidenceCode.TEST_PARSE_FAILED,
                "test file is not valid Python source",
                evidence.path,
            )
            if cache is not None:
                _cache_test_symbols(cache, cache_key, parse_error)
            return deepcopy(parse_error)
        symbols = _collect_pytest_symbols(tree)
        if cache is not None:
            _cache_test_symbols(cache, cache_key, symbols)
    elif isinstance(cached, EvidenceResult):
        return deepcopy(cached)
    else:
        symbols = cached
    if tuple(evidence.symbol.split(".")) not in symbols:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.TEST_SYMBOL_MISSING,
            "named pytest symbol is missing from the pinned tree",
            evidence.path,
            {"symbol": evidence.symbol},
        )
    return EvidenceResult(
        evidence.type,
        True,
        EvidenceCode.VERIFIED,
        "named pytest symbol exists in the pinned tree",
        evidence.path,
        {"symbol": evidence.symbol},
    )


_MISSING = object()
_ARRAY_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _resolve_pointer(document: Any, pointer: str) -> Any:
    current = document
    if not pointer:
        return current
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            if _ARRAY_INDEX.fullmatch(token) is None:
                return _MISSING
            if len(token) > len(str(len(current))):
                return _MISSING
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unsupported"


def _json_values_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return False
        return all(
            _json_values_equal(left_value, right[left_key]) for left_key, left_value in left.items()
        )
    return bool(left == right)


def _json_value_digest(
    value: Any,
    *,
    memo: dict[int, tuple[Any, bytes]] | None = None,
) -> bytes:
    """Return a type-exact Merkle digest for one JSON value."""
    value_type = type(value)
    if value_type in {list, dict} and memo is not None:
        cached = memo.get(id(value))
        if cached is not None and cached[0] is value:
            return cached[1]

    digest = hashlib.sha256()
    if value is None:
        digest.update(b"N")
    elif value_type is bool:
        digest.update(b"B1" if value else b"B0")
    elif value_type is int:
        magnitude = abs(value)
        encoded = magnitude.to_bytes(max(1, (magnitude.bit_length() + 7) // 8), "big")
        digest.update(b"I")
        digest.update(b"-" if value < 0 else b"+")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    elif value_type is float:
        normalized = 0.0 if value == 0.0 else value
        encoded = normalized.hex().encode("ascii")
        digest.update(b"F")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    elif value_type is str:
        encoded = value.encode("utf-8", errors="surrogatepass")
        digest.update(b"S")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    elif value_type is list:
        digest.update(b"L")
        digest.update(len(value).to_bytes(8, "big"))
        has_composite_child = False
        for item in value:
            has_composite_child = has_composite_child or type(item) in {list, dict}
            digest.update(_json_value_digest(item, memo=memo))
    elif value_type is dict:
        if any(type(key) is not str for key in value):
            raise TypeError("membership index received a non-JSON value")
        digest.update(b"D")
        digest.update(len(value).to_bytes(8, "big"))
        has_composite_child = False
        for key in sorted(value):
            has_composite_child = has_composite_child or type(value[key]) in {list, dict}
            digest.update(_json_value_digest(key, memo=memo))
            digest.update(_json_value_digest(value[key], memo=memo))
    else:
        raise TypeError("membership index received a non-JSON value")
    result = digest.digest()
    if (
        value_type in {list, dict}
        and memo is not None
        and (len(value) >= STRUCTURAL_DIGEST_MEMO_MIN_ITEMS or has_composite_child)
    ):
        memo[id(value)] = (value, result)
    return result


_SortableScalar = TypeVar("_SortableScalar", int, float, str)


def _sorted_unique(values: list[_SortableScalar]) -> tuple[_SortableScalar, ...]:
    values.sort()
    if not values:
        return ()
    unique = [values[0]]
    for value in values[1:]:
        if value != unique[-1]:
            unique.append(value)
    return tuple(unique)


def _build_scalar_membership_index(
    values: list[Any],
    *,
    composites: dict[bytes, list[Any]] | None = None,
    structural_digest_memo: dict[int, tuple[Any, bytes]] | None = None,
) -> _ScalarMembershipIndex:
    has_null = False
    has_false = False
    has_true = False
    integers: list[int] = []
    floats: list[float] = []
    strings: list[str] = []
    for value in values:
        value_type = type(value)
        if value is None:
            has_null = True
        elif value_type is bool:
            if value:
                has_true = True
            else:
                has_false = True
        elif value_type is int:
            integers.append(value)
        elif value_type is float:
            floats.append(value)
        elif value_type is str:
            strings.append(value)
        elif composites is not None and value_type in {list, dict}:
            candidates = composites.setdefault(
                _json_value_digest(value, memo=structural_digest_memo),
                [],
            )
            if not any(_json_values_equal(candidate, value) for candidate in candidates):
                candidates.append(value)
    booleans = tuple(value for value, present in ((False, has_false), (True, has_true)) if present)
    return _ScalarMembershipIndex(
        has_null,
        booleans,
        _sorted_unique(integers),
        _sorted_unique(floats),
        _sorted_unique(strings),
    )


def _build_list_membership_index(
    values: list[Any],
    *,
    structural_digest_memo: dict[int, tuple[Any, bytes]] | None = None,
) -> _ListMembershipIndex:
    composites: dict[bytes, list[Any]] = {}
    scalars = _build_scalar_membership_index(
        values,
        composites=composites,
        structural_digest_memo=structural_digest_memo,
    )
    groups = sorted(composites.items())
    return _ListMembershipIndex(
        scalars,
        tuple(digest for digest, _ in groups),
        tuple(tuple(candidates) for _, candidates in groups),
    )


def _artifact_check_passes(
    value: Any,
    check: ArtifactCheck,
    *,
    scalar_contains_cache: dict[
        int,
        tuple[list[Any], _ListMembershipIndex],
    ]
    | None = None,
    scalar_contains_owner: set[int] | None = None,
    structural_digest_memo: dict[int, tuple[Any, bytes]] | None = None,
    expected_digest_memo: dict[int, tuple[Any, bytes]] | None = None,
) -> bool:
    if check.operator == ArtifactOperator.EXISTS:
        return value is not _MISSING
    if value is _MISSING:
        return False
    if check.operator == ArtifactOperator.EQUALS:
        return _json_values_equal(value, check.expected)
    if check.operator == ArtifactOperator.NOT_EQUALS:
        return not _json_values_equal(value, check.expected)
    if check.operator == ArtifactOperator.CONTAINS:
        if isinstance(value, dict):
            return isinstance(check.expected, str) and check.expected in value
        if isinstance(value, list):
            if (
                type(check.expected) in {type(None), bool, int, float, str, list, dict}
                and scalar_contains_cache is not None
            ):
                cache_key = id(value)
                cached = scalar_contains_cache.get(cache_key)
                if cached is None or cached[0] is not value:
                    cached = (
                        value,
                        _build_list_membership_index(
                            value,
                            structural_digest_memo=structural_digest_memo,
                        ),
                    )
                    scalar_contains_cache[cache_key] = cached
                if scalar_contains_owner is not None:
                    scalar_contains_owner.add(cache_key)
                return cached[1].contains(
                    check.expected,
                    structural_digest_memo=expected_digest_memo,
                )
            return any(_json_values_equal(item, check.expected) for item in value)
        if isinstance(value, str):
            return isinstance(check.expected, str) and check.expected in value
        return False
    if check.operator in {
        ArtifactOperator.GREATER_THAN_OR_EQUAL,
        ArtifactOperator.LESS_THAN_OR_EQUAL,
    }:
        if (
            isinstance(value, bool)
            or isinstance(check.expected, bool)
            or not isinstance(value, (int, float))
            or not isinstance(check.expected, (int, float))
        ):
            return False
        if check.operator == ArtifactOperator.GREATER_THAN_OR_EQUAL:
            return bool(value >= check.expected)
        return bool(value <= check.expected)
    if check.operator == ArtifactOperator.TYPE:
        return bool(_json_type(value) == check.expected)
    return False


def verify_artifact(
    evidence: ArtifactEvidence,
    repository: GitRepository,
    commit: str,
    max_bytes: int,
    *,
    cache: _ArtifactCache | None = None,
    work_budget: _EvaluationWorkBudget | None = None,
) -> EvidenceResult:
    cache_key = (repository, commit, evidence.path, max_bytes, evidence.format)
    cached = cache.values.get(cache_key) if cache is not None else None
    if isinstance(cached, EvidenceResult):
        return deepcopy(cached)
    material = cached
    if (
        cache is not None
        and material is not None
        and material.valid
        and cache_key in cache.retained_order
    ):
        _touch_retained_key(cache.retained_order, cache_key)
    if (
        material is not None
        and not material.parsed
        and evidence.sha256 is not None
        and material.digest != evidence.sha256
    ):
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.DIGEST_MISMATCH,
            "artifact digest does not match the scorecard",
            evidence.path,
        )
    if cache is not None and cache.budget_exhausted and (material is None or not material.parsed):
        limit_result = EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.EVALUATION_LIMIT,
            "artifact exceeds the cumulative structured-data evaluation budget",
            evidence.path,
        )
        cache.values[cache_key] = limit_result
        return deepcopy(limit_result)
    if material is None or not material.parsed:
        try:
            content, error = _read_evidence_blob(
                repository,
                commit,
                evidence.path,
                max_bytes,
                evidence.type,
                work_budget,
            )
        except ProofStateError:
            internal_error = EvidenceResult(
                evidence.type,
                False,
                EvidenceCode.INTERNAL_ERROR,
                "Git object verification failed closed",
                evidence.path,
            )
            if cache is not None:
                cache.values[cache_key] = internal_error
            return deepcopy(internal_error)
        if error is not None:
            if cache is not None:
                cache.values[cache_key] = error
            return deepcopy(error)
        assert content is not None
        digest = hashlib.sha256(content).hexdigest()
        if evidence.sha256 is not None and digest != evidence.sha256:
            if cache is not None:
                cache.values[cache_key] = _ArtifactMaterial(digest)
            return EvidenceResult(
                evidence.type,
                False,
                EvidenceCode.DIGEST_MISMATCH,
                "artifact digest does not match the scorecard",
                evidence.path,
            )
        if cache is not None and (
            cache.budget_exhausted
            or cache.retained_input_bytes + len(content) > ARTIFACT_CACHE_MAX_INPUT_BYTES
        ):
            cache.budget_exhausted = True
            material = _ArtifactMaterial(digest, parsed=True, resource_limited=True)
            cache.values[cache_key] = material
        else:
            try:
                document = load_document(content, format_hint=evidence.format)
            except DocumentError:
                material = _ArtifactMaterial(
                    digest,
                    parsed=True,
                    input_bytes=len(content),
                    node_count=MAX_DOCUMENT_NODES,
                )
                if cache is not None and not _retain_artifact_material(cache, cache_key, material):
                    limited_material = cache.values[cache_key]
                    assert isinstance(limited_material, _ArtifactMaterial)
                    material = limited_material
            else:
                material = _ArtifactMaterial(
                    digest,
                    parsed=True,
                    valid=True,
                    document=document,
                    input_bytes=len(content),
                    node_count=count_document_nodes(document),
                )
                if cache is not None and not _retain_artifact_material(cache, cache_key, material):
                    limited_material = cache.values[cache_key]
                    assert isinstance(limited_material, _ArtifactMaterial)
                    material = limited_material
    assert material is not None
    if evidence.sha256 is not None and material.digest != evidence.sha256:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.DIGEST_MISMATCH,
            "artifact digest does not match the scorecard",
            evidence.path,
        )
    if material.resource_limited:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.EVALUATION_LIMIT,
            "artifact exceeds the cumulative structured-data evaluation budget",
            evidence.path,
        )
    if not material.valid:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ARTIFACT_INVALID,
            "artifact is not valid bounded structured data",
            evidence.path,
        )
    document = material.document
    scalar_contains_cache: dict[
        int,
        tuple[list[Any], _ListMembershipIndex],
    ] = {}
    retained_material = cache is not None and cache.values.get(cache_key) is material
    scalar_contains_owner: set[int] | None = None
    if retained_material:
        assert cache is not None
        scalar_contains_cache = cache.scalar_contains
        scalar_contains_owner = cache.scalar_owners.setdefault(cache_key, set())
    expected_digest_memo: dict[int, tuple[Any, bytes]] = {}
    failures = [
        index
        for index, check in enumerate(evidence.checks)
        if not _artifact_check_passes(
            _resolve_pointer(document, check.pointer),
            check,
            scalar_contains_cache=scalar_contains_cache,
            scalar_contains_owner=scalar_contains_owner,
            structural_digest_memo=material.structural_digests,
            expected_digest_memo=expected_digest_memo,
        )
    ]
    if failures:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ARTIFACT_CHECK_FAILED,
            "one or more structured artifact checks failed",
            evidence.path,
            {"failed_check_indexes": failures},
        )
    return EvidenceResult(
        evidence.type,
        True,
        EvidenceCode.VERIFIED,
        "all structured artifact checks passed",
        evidence.path,
        {"checks": len(evidence.checks), "sha256": material.digest},
    )


def verify_attestation(
    evidence: AttestationEvidence,
    repository: GitRepository,
    policy_commit: str,
    target_commit: str,
    repository_identity: str,
    assertion_id: str,
    evaluated_at: datetime,
    max_bytes: int,
    *,
    cache: _AttestationCache | None = None,
    work_budget: _EvaluationWorkBudget | None = None,
) -> EvidenceResult:
    cache_key = (repository, policy_commit, evidence.path, max_bytes)
    cached = cache.values.get(cache_key) if cache is not None else None
    if isinstance(cached, EvidenceResult):
        return deepcopy(cached)
    material = cached
    if (
        cache is not None
        and material is not None
        and material.attestation is not None
        and cache_key in cache.retained_order
    ):
        _touch_retained_key(cache.retained_order, cache_key)
    if (
        material is not None
        and not material.parsed
        and evidence.sha256 is not None
        and material.digest != evidence.sha256
    ):
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.DIGEST_MISMATCH,
            "attestation digest does not match the scorecard",
            evidence.path,
        )
    if cache is not None and cache.budget_exhausted and (material is None or not material.parsed):
        limit_result = EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.EVALUATION_LIMIT,
            "attestation exceeds the cumulative structured-data evaluation budget",
            evidence.path,
        )
        cache.values[cache_key] = limit_result
        return deepcopy(limit_result)
    if material is None or not material.parsed:
        try:
            content, error = _read_evidence_blob(
                repository,
                policy_commit,
                evidence.path,
                max_bytes,
                evidence.type,
                work_budget,
            )
        except ProofStateError:
            internal_error = EvidenceResult(
                evidence.type,
                False,
                EvidenceCode.INTERNAL_ERROR,
                "Git object verification failed closed",
                evidence.path,
            )
            if cache is not None:
                cache.values[cache_key] = internal_error
            return deepcopy(internal_error)
        if error is not None:
            if cache is not None:
                cache.values[cache_key] = error
            return deepcopy(error)
        assert content is not None
        digest = hashlib.sha256(content).hexdigest()
        if evidence.sha256 is not None and digest != evidence.sha256:
            if cache is not None:
                cache.values[cache_key] = _AttestationMaterial(digest)
            return EvidenceResult(
                evidence.type,
                False,
                EvidenceCode.DIGEST_MISMATCH,
                "attestation digest does not match the scorecard",
                evidence.path,
            )
        if cache is not None and (
            cache.budget_exhausted
            or cache.retained_input_bytes + len(content) > ATTESTATION_CACHE_MAX_INPUT_BYTES
        ):
            cache.budget_exhausted = True
            material = _AttestationMaterial(digest, parsed=True, resource_limited=True)
            cache.values[cache_key] = material
        else:
            try:
                document = load_document(content)
            except DocumentError:
                material = _AttestationMaterial(
                    digest,
                    parsed=True,
                    input_bytes=len(content),
                    node_count=MAX_DOCUMENT_NODES,
                )
            else:
                node_count = count_document_nodes(document)
                try:
                    attestation = HumanAttestation.model_validate(document)
                except ValidationError:
                    material = _AttestationMaterial(
                        digest,
                        parsed=True,
                        input_bytes=len(content),
                        node_count=node_count,
                    )
                else:
                    material = _AttestationMaterial(
                        digest,
                        parsed=True,
                        attestation=attestation,
                        input_bytes=len(content),
                        node_count=node_count,
                    )
            if cache is not None and not _retain_attestation_material(cache, cache_key, material):
                limited_attestation = cache.values[cache_key]
                assert isinstance(limited_attestation, _AttestationMaterial)
                material = limited_attestation
    assert material is not None
    if evidence.sha256 is not None and material.digest != evidence.sha256:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.DIGEST_MISMATCH,
            "attestation digest does not match the scorecard",
            evidence.path,
        )
    if material.resource_limited:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.EVALUATION_LIMIT,
            "attestation exceeds the cumulative structured-data evaluation budget",
            evidence.path,
        )
    if material.attestation is None:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ATTESTATION_INVALID,
            "attestation does not conform to the versioned schema",
            evidence.path,
        )
    attestation = material.attestation
    if evaluated_at < attestation.issued_at:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ATTESTATION_NOT_YET_VALID,
            "attestation issue time is in the future",
            evidence.path,
        )
    if evaluated_at >= attestation.expires_at:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ATTESTATION_EXPIRED,
            "attestation has expired",
            evidence.path,
        )
    scope = attestation.scope
    if (
        scope.repository != repository_identity
        or scope.commit != target_commit
        or assertion_id not in scope.assertions
    ):
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.ATTESTATION_SCOPE_MISMATCH,
            "attestation does not cover this repository, commit, and assertion",
            evidence.path,
        )
    return EvidenceResult(
        evidence.type,
        True,
        EvidenceCode.VERIFIED,
        "human attestation is current and correctly scoped",
        evidence.path,
        {
            "identity": attestation.identity,
            "issued_at": attestation.issued_at.isoformat(),
            "expires_at": attestation.expires_at.isoformat(),
        },
    )


def verify_machine_evidence(
    evidence: MachineEvidence,
    repository: GitRepository,
    commit: str,
    max_bytes: int,
    *,
    artifact_cache: _ArtifactCache | None = None,
    file_digest_cache: _FileDigestCache | None = None,
    test_symbol_cache: _TestSymbolCache | None = None,
    work_budget: _EvaluationWorkBudget | None = None,
) -> EvidenceResult:
    try:
        if isinstance(evidence, FileEvidence):
            return verify_file(
                evidence,
                repository,
                commit,
                max_bytes,
                cache=file_digest_cache,
                work_budget=work_budget,
            )
        if isinstance(evidence, TestSymbolEvidence):
            return verify_test_symbol(
                evidence,
                repository,
                commit,
                max_bytes,
                cache=test_symbol_cache,
                work_budget=work_budget,
            )
        return verify_artifact(
            evidence,
            repository,
            commit,
            max_bytes,
            cache=artifact_cache,
            work_budget=work_budget,
        )
    except ProofStateError:
        return EvidenceResult(
            evidence.type,
            False,
            EvidenceCode.INTERNAL_ERROR,
            "Git object verification failed closed",
            evidence.path,
        )
