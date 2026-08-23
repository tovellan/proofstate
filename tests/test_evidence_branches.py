from __future__ import annotations

import ast
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, NoReturn, cast

import pytest

from proofstate.document import MAX_DOCUMENT_NODES, count_document_nodes
from proofstate.errors import ErrorCode, ProofStateError
from proofstate.evidence import (
    ARTIFACT_CACHE_MAX_INPUT_BYTES,
    ARTIFACT_CACHE_MAX_NODES,
    ATTESTATION_CACHE_MAX_INPUT_BYTES,
    TEST_SOURCE_MAX_BYTES,
    EvidenceCode,
    EvidenceResult,
    _artifact_check_passes,
    _ArtifactCache,
    _ArtifactMaterial,
    _AttestationCache,
    _AttestationMaterial,
    _build_list_membership_index,
    _build_scalar_membership_index,
    _collect_pytest_symbols,
    _EvaluationWorkBudget,
    _json_type,
    _json_value_digest,
    _json_values_equal,
    _resolve_pointer,
    _retain_artifact_material,
    _retain_attestation_material,
    verify_artifact,
    verify_attestation,
    verify_file,
    verify_machine_evidence,
    verify_test_symbol,
)
from proofstate.git import GitRepository, TreeEntry
from proofstate.models import (
    ArtifactCheck,
    ArtifactEvidence,
    ArtifactOperator,
    AttestationEvidence,
    FileEvidence,
)
from proofstate.models import (
    TestSymbolEvidence as SymbolEvidence,
)


class BlobRepository:
    def __init__(self, content: bytes | Exception) -> None:
        self.content = content

    def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
        del commit, path, max_bytes
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


def repository_with(content: bytes | Exception) -> GitRepository:
    return cast(GitRepository, cast(Any, BlobRepository(content)))


def check(
    operator: ArtifactOperator, expected: Any = None, *, supplied: bool = True
) -> ArtifactCheck:
    values: dict[str, Any] = {"pointer": "", "operator": operator}
    if supplied:
        values["expected"] = expected
    return ArtifactCheck.model_validate(values)


def test_evidence_result_omits_empty_optional_fields() -> None:
    result = EvidenceResult("file", True, EvidenceCode.VERIFIED, "ok")
    assert result.to_dict() == {
        "type": "file",
        "passed": True,
        "code": "PSE000_VERIFIED",
        "message": "ok",
    }


def test_serialized_evidence_details_do_not_alias_the_result() -> None:
    result = EvidenceResult(
        "artifact",
        False,
        EvidenceCode.ARTIFACT_CHECK_FAILED,
        "failed",
        "report.json",
        {"failed_check_indexes": [0]},
    )

    first = result.to_dict()
    details = cast(dict[str, Any], first["details"])
    cast(list[int], details["failed_check_indexes"]).append(9)

    assert result.details == {"failed_check_indexes": [0]}
    assert result.to_dict()["details"] == {"failed_check_indexes": [0]}


def test_evaluation_work_budget_rejects_nonregular_unsized_and_oversized_entries() -> None:
    class EntryRepository:
        def __init__(self, entry: TreeEntry | None) -> None:
            self.current = entry

        def entry(self, commit: str, path: str) -> TreeEntry | None:
            del commit, path
            return self.current

    repository = EntryRepository(None)
    typed_repository = cast(GitRepository, cast(Any, repository))
    budget = _EvaluationWorkBudget()

    with pytest.raises(FileNotFoundError):
        budget.reserve(typed_repository, "a" * 40, "evidence/result", 10)
    repository.current = TreeEntry("100644", "blob", "b" * 40, "evidence/result")
    with pytest.raises(ProofStateError, match="blob size"):
        budget.reserve(typed_repository, "a" * 40, "evidence/result", 10)
    repository.current = TreeEntry("100644", "blob", "b" * 40, "evidence/result", 11)
    with pytest.raises(OverflowError):
        budget.reserve(typed_repository, "a" * 40, "evidence/result", 10)


@pytest.mark.parametrize(
    ("document", "pointer", "expected"),
    [
        ({"a/b": {"~key": 3}}, "/a~1b/~0key", 3),
        (["zero"], "/0", "zero"),
        (["zero"], "/x", None),
        (["zero"], "/2", None),
        ({"value": 1}, "/value/deeper", None),
        ({"value": 1}, "/missing", None),
        ({"value": 1}, "", {"value": 1}),
    ],
)
def test_json_pointer_resolution(document: Any, pointer: str, expected: Any) -> None:
    resolved = _resolve_pointer(document, pointer)
    if expected is None:
        assert type(resolved) is object
    else:
        assert resolved == expected


def test_oversized_json_pointer_index_is_missing() -> None:
    assert type(_resolve_pointer([], "/" + "9" * 5_000)) is object


@pytest.mark.parametrize("pointer", ["/00", "/01", "/0001", "/\u0661", "/-", "/+1"])
def test_array_pointer_indexes_must_be_canonical_ascii_decimal(pointer: str) -> None:
    assert type(_resolve_pointer(["zero", "one"], pointer)) is object


@pytest.mark.parametrize(("pointer", "expected"), [("/0", "zero"), ("/1", "one")])
def test_canonical_array_pointer_indexes_are_resolved(pointer: str, expected: str) -> None:
    assert _resolve_pointer(["zero", "one"], pointer) == expected


@pytest.mark.parametrize(
    ("document", "pointer", "expected"),
    [
        ({"01": "leading"}, "/01", "leading"),
        ({"\u0661": "unicode"}, "/\u0661", "unicode"),
        ({"": "empty"}, "/", "empty"),
    ],
)
def test_object_pointer_tokens_are_not_restricted_as_array_indexes(
    document: dict[str, str], pointer: str, expected: str
) -> None:
    assert _resolve_pointer(document, pointer) == expected


@pytest.mark.parametrize(
    ("value", "operator", "expected", "passed"),
    [
        (1, ArtifactOperator.EQUALS, 1, True),
        (True, ArtifactOperator.EQUALS, 1, False),
        (1, ArtifactOperator.NOT_EQUALS, 2, True),
        ({"key": 1}, ArtifactOperator.CONTAINS, "key", True),
        ({"key": 1}, ArtifactOperator.CONTAINS, [], False),
        ({1: "value"}, ArtifactOperator.CONTAINS, True, False),
        ([1], ArtifactOperator.CONTAINS, True, False),
        ([{"flag": 1}], ArtifactOperator.CONTAINS, {"flag": True}, False),
        ([[1]], ArtifactOperator.CONTAINS, [True], False),
        ("ready", ArtifactOperator.CONTAINS, "ead", True),
        ("ready", ArtifactOperator.CONTAINS, 1, False),
        (7, ArtifactOperator.CONTAINS, 7, False),
        (7, ArtifactOperator.GREATER_THAN_OR_EQUAL, 7, True),
        (7, ArtifactOperator.LESS_THAN_OR_EQUAL, 8, True),
        (True, ArtifactOperator.GREATER_THAN_OR_EQUAL, 1, False),
        (None, ArtifactOperator.TYPE, "null", True),
        (True, ArtifactOperator.TYPE, "boolean", True),
        (1, ArtifactOperator.TYPE, "number", True),
        ("x", ArtifactOperator.TYPE, "string", True),
        ([], ArtifactOperator.TYPE, "array", True),
        ({}, ArtifactOperator.TYPE, "object", True),
    ],
)
def test_artifact_operator_semantics(
    value: Any,
    operator: ArtifactOperator,
    expected: Any,
    passed: bool,
) -> None:
    assert _artifact_check_passes(value, check(operator, expected)) is passed


def test_unsupported_json_type_is_explicit() -> None:
    assert _json_type(object()) == "unsupported"


def test_file_digest_mismatch_and_size_limit() -> None:
    mismatch = verify_file(
        FileEvidence(type="file", path="file.txt", sha256="0" * 64),
        repository_with(b"content"),
        "a" * 40,
        100,
    )
    oversized = verify_file(
        FileEvidence(type="file", path="file.txt"),
        repository_with(OverflowError()),
        "a" * 40,
        1,
    )
    assert mismatch.code == EvidenceCode.DIGEST_MISMATCH
    assert oversized.code == EvidenceCode.FILE_TOO_LARGE


def test_invalid_python_source_is_rejected() -> None:
    result = verify_test_symbol(
        SymbolEvidence(
            type="test_symbol",
            path="tests/test_invalid.py",
            symbol="test_invalid",
            framework="pytest",
        ),
        repository_with(b"def test_invalid(:\n"),
        "a" * 40,
        100,
    )
    assert result.code == EvidenceCode.TEST_PARSE_FAILED


def test_test_source_cap_is_applied_before_ast_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    class LimitRecordingRepository:
        def __init__(self, content: bytes) -> None:
            self.content = content
            self.requested_max_bytes: int | None = None

        def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
            del commit, path
            self.requested_max_bytes = max_bytes
            if len(self.content) > max_bytes:
                raise OverflowError
            return self.content

    content = b"x=0\n" * (TEST_SOURCE_MAX_BYTES // 4 + 1)
    repository = LimitRecordingRepository(content)

    def reject_parse(*args: Any, **kwargs: Any) -> NoReturn:
        del args, kwargs
        raise AssertionError("oversized test source reached ast.parse")

    monkeypatch.setattr(ast, "parse", reject_parse)
    result = verify_test_symbol(
        SymbolEvidence(
            type="test_symbol",
            path="tests/test_large.py",
            symbol="test_present",
            framework="pytest",
        ),
        cast(GitRepository, cast(Any, repository)),
        "a" * 40,
        10_485_760,
    )

    assert len(content) > TEST_SOURCE_MAX_BYTES
    assert repository.requested_max_bytes == TEST_SOURCE_MAX_BYTES
    assert result.code == EvidenceCode.FILE_TOO_LARGE


def test_test_source_at_cap_is_parsed() -> None:
    prefix = b"def test_present():\n    pass\n#"
    content = prefix + b"x" * (TEST_SOURCE_MAX_BYTES - len(prefix) - 1) + b"\n"

    result = verify_test_symbol(
        SymbolEvidence(
            type="test_symbol",
            path="tests/test_large.py",
            symbol="test_present",
            framework="pytest",
        ),
        repository_with(content),
        "a" * 40,
        TEST_SOURCE_MAX_BYTES,
    )

    assert len(content) == TEST_SOURCE_MAX_BYTES
    assert result.code == EvidenceCode.VERIFIED


def test_distinct_symbols_from_one_file_share_blob_and_ast_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CountingRepository:
        def __init__(self) -> None:
            self.reads = 0

        def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
            del commit, path, max_bytes
            self.reads += 1
            return b"def test_one():\n    pass\n\ndef test_two():\n    pass\n"

    repository = CountingRepository()
    original_parse = ast.parse
    parses = 0

    def counting_parse(*args: Any, **kwargs: Any) -> ast.AST:
        nonlocal parses
        parses += 1
        return cast(ast.AST, original_parse(*args, **kwargs))

    monkeypatch.setattr(ast, "parse", counting_parse)
    cache: dict[
        tuple[GitRepository, str, str, int],
        set[tuple[str, ...]] | EvidenceResult,
    ] = {}
    results = [
        verify_test_symbol(
            SymbolEvidence(
                type="test_symbol",
                path="tests/test_shared.py",
                symbol=symbol,
                framework="pytest",
            ),
            cast(GitRepository, cast(Any, repository)),
            "a" * 40,
            TEST_SOURCE_MAX_BYTES,
            cache=cache,
        )
        for symbol in ("test_one", "test_two")
    ]

    assert [result.code for result in results] == [EvidenceCode.VERIFIED] * 2
    assert repository.reads == 1
    assert parses == 1


def test_test_symbol_cache_retains_all_sources_admitted_by_evaluation_budget() -> None:
    repository = BlobRepository(b"def test_present():\n    pass\n")
    typed_repository = cast(GitRepository, cast(Any, repository))
    cache: dict[
        tuple[GitRepository, str, str, int],
        set[tuple[str, ...]] | EvidenceResult,
    ] = {}

    source_count = 65
    for index in range(source_count):
        result = verify_test_symbol(
            SymbolEvidence(
                type="test_symbol",
                path=f"tests/test_{index}.py",
                symbol="test_present",
                framework="pytest",
            ),
            typed_repository,
            "a" * 40,
            TEST_SOURCE_MAX_BYTES,
            cache=cache,
        )
        assert result.code == EvidenceCode.VERIFIED

    assert len(cache) == source_count
    cached_paths = [key[2] for key in cache]
    assert cached_paths[0] == "tests/test_0.py"
    assert cached_paths[-1] == f"tests/test_{source_count - 1}.py"


def test_collected_class_symbols_share_one_class_name_object() -> None:
    class_name = f"Test{'x' * 10_000}"
    tree = ast.parse(
        f"class {class_name}:\n"
        "    def test_one(self): pass\n"
        "    def test_two(self): pass\n"
        "    def test_three(self): pass\n"
    )

    symbols = _collect_pytest_symbols(tree)

    assert symbols == {
        (class_name, "test_one"),
        (class_name, "test_two"),
        (class_name, "test_three"),
    }
    class_components = [symbol[0] for symbol in symbols]
    assert all(component is class_components[0] for component in class_components)


def test_symbol_collection_ignores_non_collectable_direct_definitions() -> None:
    tree = ast.parse(
        "def helper(): pass\n"
        "def test_top(): pass\n"
        "class Helper:\n"
        "    def test_method(self): pass\n"
        "class TestReady:\n"
        "    def helper(self): pass\n"
        "    def test_method(self): pass\n"
    )

    assert _collect_pytest_symbols(tree) == {
        ("test_top",),
        ("TestReady", "test_method"),
    }


@pytest.mark.parametrize(
    ("content", "symbol"),
    [
        (b"if False:\n    def test_hidden():\n        pass\n", "test_hidden"),
        (
            b"class TestHidden:\n    if False:\n        def test_method(self):\n            pass\n",
            "TestHidden.test_method",
        ),
        (
            b"if False:\n    class TestHidden:\n        def test_method(self):\n            pass\n",
            "TestHidden.test_method",
        ),
    ],
)
def test_conditional_pytest_symbols_are_not_treated_as_collectable(
    content: bytes,
    symbol: str,
) -> None:
    result = verify_test_symbol(
        SymbolEvidence(
            type="test_symbol",
            path="tests/test_hidden.py",
            symbol=symbol,
            framework="pytest",
        ),
        repository_with(content),
        "a" * 40,
        10_000,
    )

    assert result.code == EvidenceCode.TEST_SYMBOL_MISSING


def test_deep_test_body_does_not_recurse_during_symbol_collection() -> None:
    expression = b"+".join([b"1"] * 500)
    content = b"def test_deep():\n    value = " + expression + b"\n"

    result = verify_test_symbol(
        SymbolEvidence(
            type="test_symbol",
            path="tests/test_deep.py",
            symbol="test_deep",
            framework="pytest",
        ),
        repository_with(content),
        "a" * 40,
        10_000,
    )

    assert result.code == EvidenceCode.VERIFIED


def test_parser_recursion_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def recurse(*args: Any, **kwargs: Any) -> NoReturn:
        del args, kwargs
        raise RecursionError

    monkeypatch.setattr(ast, "parse", recurse)

    result = verify_test_symbol(
        SymbolEvidence(
            type="test_symbol",
            path="tests/test_deep.py",
            symbol="test_deep",
            framework="pytest",
        ),
        repository_with(b"def test_deep():\n    pass\n"),
        "a" * 40,
        10_000,
    )

    assert result.code == EvidenceCode.TEST_PARSE_FAILED


def test_invalid_artifact_and_digest_are_rejected() -> None:
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "report.json",
            "format": "json",
            "checks": [{"pointer": "", "operator": "exists"}],
        }
    )
    invalid = verify_artifact(evidence, repository_with(b"{"), "a" * 40, 100)
    evidence_with_digest = evidence.model_copy(update={"sha256": "0" * 64})
    mismatch = verify_artifact(
        evidence_with_digest,
        repository_with(b"{}"),
        "a" * 40,
        100,
    )
    assert invalid.code == EvidenceCode.ARTIFACT_INVALID
    assert mismatch.code == EvidenceCode.DIGEST_MISMATCH


def test_out_of_range_yaml_escape_fails_artifact_closed() -> None:
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "evidence/result.yaml",
            "format": "yaml",
            "checks": [{"pointer": "", "operator": "exists"}],
        }
    )

    result = verify_artifact(
        evidence,
        repository_with(b'"\\UFFFFFFFF"'),
        "a" * 40,
        1_000,
        cache=_ArtifactCache(),
    )

    assert result.code == EvidenceCode.ARTIFACT_INVALID


@pytest.mark.parametrize(
    "content",
    [
        b"outer:\n  .inf: value\n",
        b"outer:\n  1: value\n",
        b"outer:\n  value: .inf\n",
    ],
)
def test_invalid_yaml_mapping_members_fail_artifact_closed(content: bytes) -> None:
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "report.yaml",
            "format": "yaml",
            "checks": [{"pointer": "", "operator": "exists"}],
        }
    )

    result = verify_artifact(evidence, repository_with(content), "a" * 40, 1_000)

    assert result.code == EvidenceCode.ARTIFACT_INVALID


def test_yaml_node_limit_fails_artifact_closed_below_default_byte_limit() -> None:
    content = b"values: [" + b"0," * MAX_DOCUMENT_NODES + b"]\n"
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "report.yaml",
            "format": "yaml",
            "checks": [{"pointer": "/values/0", "operator": "equals", "expected": 0}],
        }
    )

    result = verify_artifact(evidence, repository_with(content), "a" * 40, 1_048_576)

    assert len(content) < 1_048_576
    assert result.code == EvidenceCode.ARTIFACT_INVALID


def test_nested_contains_uses_type_exact_composite_equality() -> None:
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "report.json",
            "format": "json",
            "checks": [
                {
                    "pointer": "/results",
                    "operator": "contains",
                    "expected": {"passed": True},
                }
            ],
        }
    )

    result = verify_artifact(
        evidence,
        repository_with(b'{"results":[{"passed":1}]}'),
        "a" * 40,
        1_000,
    )

    assert result.code == EvidenceCode.ARTIFACT_CHECK_FAILED
    assert result.details == {"failed_check_indexes": [0]}


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (1, 1.0),
        ([1], [1.0]),
        ({"value": 1}, {"value": 1.0}),
    ],
)
def test_json_equality_distinguishes_integer_and_float_representations(
    left: object,
    right: object,
) -> None:
    assert _json_values_equal(left, right) is False


def test_large_reversed_object_equality_passes_through_artifact_verification() -> None:
    size = 30_000
    observed = {f"key-{index:05d}": index for index in range(size)}
    expected = {f"key-{index:05d}": index for index in reversed(range(size))}
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "report.json",
            "format": "json",
            "checks": [{"pointer": "/results", "operator": "equals", "expected": expected}],
        }
    )
    content = json.dumps({"results": observed}, separators=(",", ":")).encode()

    result = verify_artifact(evidence, repository_with(content), "a" * 40, 1_048_576)

    assert len(content) < 1_048_576
    assert result.code == EvidenceCode.VERIFIED


def test_object_equality_uses_one_direct_lookup_per_key() -> None:
    class LookupCountingDict(dict[str, int]):
        def __init__(self, values: dict[str, int], *, allow_items: bool) -> None:
            super().__init__(values)
            self.allow_items = allow_items
            self.lookups = 0

        def items(self) -> Any:
            if not self.allow_items:
                raise AssertionError("right-side items must not be scanned")
            return super().items()

        def __getitem__(self, key: str) -> int:
            self.lookups += 1
            return super().__getitem__(key)

    size = 30_000
    left = LookupCountingDict(
        {f"key-{index:05d}": index for index in range(size)},
        allow_items=True,
    )
    right = LookupCountingDict(
        {f"key-{index:05d}": index for index in reversed(range(size))},
        allow_items=False,
    )

    assert _json_values_equal(left, right) is True
    assert right.lookups == size


def test_object_contains_uses_one_direct_key_lookup() -> None:
    class LookupCountingDict(dict[str, int]):
        def __init__(self) -> None:
            super().__init__({"present": 1})
            self.lookups = 0

        def __iter__(self) -> NoReturn:
            raise AssertionError("object keys must not be scanned")

        def __contains__(self, key: object) -> bool:
            self.lookups += 1
            return super().__contains__(key)

    value = LookupCountingDict()

    assert _artifact_check_passes(value, check(ArtifactOperator.CONTAINS, "absent")) is False
    assert value.lookups == 1


def test_scalar_list_contains_builds_one_type_exact_membership_index() -> None:
    class IterationCountingList(list[int]):
        def __init__(self, values: list[int]) -> None:
            super().__init__(values)
            self.iterations = 0

        def __iter__(self) -> Iterator[int]:
            self.iterations += 1
            return super().__iter__()

    value = IterationCountingList(list(range(10_000)))
    scalar_cache: dict[Any, Any] = {}

    assert (
        _artifact_check_passes(
            value,
            check(ArtifactOperator.CONTAINS, 10_001),
            scalar_contains_cache=scalar_cache,
        )
        is False
    )
    assert (
        _artifact_check_passes(
            value,
            check(ArtifactOperator.CONTAINS, 9_999),
            scalar_contains_cache=scalar_cache,
        )
        is True
    )
    assert (
        _artifact_check_passes(
            value,
            check(ArtifactOperator.CONTAINS, 9_999.0),
            scalar_contains_cache=scalar_cache,
        )
        is False
    )
    assert value.iterations == 1


def test_composite_list_contains_builds_one_bounded_membership_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IterationCountingList(list[Any]):
        def __init__(self, values: list[Any]) -> None:
            super().__init__(values)
            self.iterations = 0

        def __iter__(self) -> Iterator[Any]:
            self.iterations += 1
            return super().__iter__()

    value = IterationCountingList([[] for _ in range(120_000)])
    membership_cache: dict[Any, Any] = {}
    digest_memo: dict[int, tuple[Any, bytes]] = {}
    equality_calls = 0
    original_equal = _json_values_equal

    def counting_equal(left: Any, right: Any) -> bool:
        nonlocal equality_calls
        equality_calls += 1
        return original_equal(left, right)

    monkeypatch.setattr("proofstate.evidence._json_values_equal", counting_equal)

    for expected in ([index] for index in range(500)):
        assert (
            _artifact_check_passes(
                value,
                check(ArtifactOperator.CONTAINS, expected),
                scalar_contains_cache=membership_cache,
                structural_digest_memo=digest_memo,
            )
            is False
        )
    assert (
        _artifact_check_passes(
            value,
            check(ArtifactOperator.CONTAINS, []),
            scalar_contains_cache=membership_cache,
            structural_digest_memo=digest_memo,
        )
        is True
    )

    assert value.iterations == 1
    assert equality_calls == len(value)
    assert digest_memo == {}


def test_composite_membership_digest_is_type_exact_and_mapping_order_independent() -> None:
    index = _build_list_membership_index(
        [
            {"number": 1, "nested": [-0.0]},
            [1],
            [1.0],
            [True],
            ["\ud800"],
        ]
    )

    assert index.contains({"nested": [0.0], "number": 1}) is True
    assert index.contains([1]) is True
    assert index.contains([1.0]) is True
    assert index.contains([True]) is True
    assert index.contains(["\ud800"]) is True
    assert index.contains({"number": 1.0, "nested": [0.0]}) is False
    assert _json_value_digest({"a": 1, "b": 2}) == _json_value_digest({"b": 2, "a": 1})


def test_nested_membership_hashes_each_document_node_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested: list[Any] = [{"leaf": list(range(100))}]
    for _ in range(40):
        nested = [nested]
    memo: dict[int, tuple[Any, bytes]] = {}
    digest_calls = 0
    original_sha256 = __import__("hashlib").sha256

    def counting_sha256(*args: Any, **kwargs: Any) -> Any:
        nonlocal digest_calls
        digest_calls += 1
        return original_sha256(*args, **kwargs)

    monkeypatch.setattr("proofstate.evidence.hashlib.sha256", counting_sha256)
    current = nested
    while current and type(current[0]) is list:
        _build_list_membership_index(current, structural_digest_memo=memo)
        current = current[0]
    _build_list_membership_index(current, structural_digest_memo=memo)

    assert digest_calls == count_document_nodes(nested) - 1


def test_composite_digest_collision_still_confirms_recursive_equality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "proofstate.evidence._json_value_digest",
        lambda value, *, memo=None: b"x" * 32,
    )
    index = _build_list_membership_index([[1]])

    assert index.contains([1]) is True
    assert index.contains([2]) is False


def test_composite_contains_accepts_large_yaml_hex_integer_without_decimal_conversion() -> None:
    digits = "f" * 5_000
    expected = int(digits, 16)
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "evidence/result.yaml",
            "format": "yaml",
            "checks": [{"pointer": "/values", "operator": "contains", "expected": [expected]}],
        }
    )

    result = verify_artifact(
        evidence,
        repository_with(f"values:\n  - [0x{digits}]\n".encode()),
        "a" * 40,
        10_000,
        cache=_ArtifactCache(),
    )

    assert result.code == EvidenceCode.VERIFIED


def test_scalar_membership_index_uses_bisection_for_colliding_integers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = [index * sys.hash_info.modulus for index in range(20_000)]
    assert {hash(value) for value in values} == {0}
    index = _build_scalar_membership_index(values)
    original_bisect = __import__("bisect").bisect_left
    bisections = 0

    def counting_bisect(sequence: Any, value: Any) -> int:
        nonlocal bisections
        bisections += 1
        return int(original_bisect(sequence, value))

    monkeypatch.setattr("proofstate.evidence.bisect_left", counting_bisect)

    assert index.contains(20_001 * sys.hash_info.modulus) is False
    assert bisections == 1


def test_artifact_cache_retains_round_robin_documents_within_resource_budgets() -> None:
    content = json.dumps({"values": [0] * 25_000}, separators=(",", ":")).encode()

    class CountingRepository:
        def __init__(self) -> None:
            self.reads: dict[str, int] = {}

        def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
            del commit, max_bytes
            self.reads[path] = self.reads.get(path, 0) + 1
            return content

    repository = CountingRepository()
    typed_repository = cast(GitRepository, cast(Any, repository))
    cache = _ArtifactCache()

    document_count = 9
    evidences = [
        ArtifactEvidence.model_validate(
            {
                "type": "artifact",
                "path": f"evidence/result-{index}.json",
                "format": "json",
                "checks": [{"pointer": "/values", "operator": "contains", "expected": 0}],
            }
        )
        for index in range(document_count)
    ]
    for _ in range(2):
        for evidence in evidences:
            assert (
                verify_artifact(
                    evidence,
                    typed_repository,
                    "a" * 40,
                    len(content),
                    cache=cache,
                ).code
                == EvidenceCode.VERIFIED
            )

    assert cache.retained_documents == document_count
    assert len(cache.retained_order) == document_count
    assert len(cache.scalar_contains) == document_count
    assert cache.retained_nodes == document_count * 25_003
    assert cache.retained_nodes < ARTIFACT_CACHE_MAX_NODES
    assert set(repository.reads.values()) == {1}


def test_artifact_cache_returns_stable_limit_result_instead_of_reparsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CountingRepository:
        def __init__(self) -> None:
            self.reads: dict[str, int] = {}

        def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
            del commit, max_bytes
            self.reads[path] = self.reads.get(path, 0) + 1
            return b'{"x":[1]}'

    monkeypatch.setattr("proofstate.evidence.ARTIFACT_CACHE_MAX_NODES", 5)
    repository = CountingRepository()
    typed_repository = cast(GitRepository, cast(Any, repository))
    cache = _ArtifactCache()
    evidences = [
        ArtifactEvidence.model_validate(
            {
                "type": "artifact",
                "path": f"evidence/result-{index}.json",
                "format": "json",
                "checks": [{"pointer": "/x", "operator": "exists"}],
            }
        )
        for index in range(2)
    ]

    first = verify_artifact(evidences[0], typed_repository, "a" * 40, 100, cache=cache)
    limited = verify_artifact(evidences[1], typed_repository, "a" * 40, 100, cache=cache)
    repeated = verify_artifact(
        evidences[1].model_copy(
            update={"checks": [check(ArtifactOperator.EXISTS, supplied=False)]}
        ),
        typed_repository,
        "a" * 40,
        100,
        cache=cache,
    )

    assert first.code == EvidenceCode.VERIFIED
    assert limited.code == EvidenceCode.EVALUATION_LIMIT
    assert repeated.code == EvidenceCode.EVALUATION_LIMIT
    assert repository.reads == {
        "evidence/result-0.json": 1,
        "evidence/result-1.json": 1,
    }


def test_material_caches_fail_closed_at_aggregate_resource_budgets() -> None:
    repository = repository_with(b"")
    artifact_cache = _ArtifactCache()
    artifact_keys = [
        (repository, "a" * 40, f"evidence/result-{index}.json", 10_000_000, "json")
        for index in range(2)
    ]
    values = [1]
    values_id = id(values)
    artifact_cache.scalar_contains[values_id] = (
        values,
        _build_list_membership_index(values),
    )
    artifact_cache.scalar_owners[artifact_keys[0]] = {values_id}
    admissions = [
        _retain_artifact_material(
            artifact_cache,
            artifact_key,
            _ArtifactMaterial(
                artifact_key[2],
                parsed=True,
                valid=True,
                document={},
                input_bytes=1,
                node_count=(ARTIFACT_CACHE_MAX_NODES // 2) + 1,
            ),
        )
        for artifact_key in artifact_keys
    ]
    assert admissions == [True, False]

    assert artifact_cache.retained_documents == 1
    assert artifact_cache.retained_nodes <= ARTIFACT_CACHE_MAX_NODES
    assert artifact_keys[0] in artifact_cache.retained_order
    assert artifact_keys[1] not in artifact_cache.retained_order
    assert values_id in artifact_cache.scalar_contains
    limited_artifact = artifact_cache.values[artifact_keys[1]]
    assert isinstance(limited_artifact, _ArtifactMaterial)
    assert limited_artifact.resource_limited is True

    attestation_cache = _AttestationCache()
    attestation_keys = [
        (
            repository,
            "b" * 40,
            f".proofstate/attestations/review-{index}.json",
            10_000_000,
        )
        for index in range(2)
    ]
    admissions = [
        _retain_attestation_material(
            attestation_cache,
            attestation_key,
            _AttestationMaterial(
                attestation_key[2],
                parsed=True,
                input_bytes=(ATTESTATION_CACHE_MAX_INPUT_BYTES // 2) + 1,
            ),
        )
        for attestation_key in attestation_keys
    ]
    assert admissions == [True, False]

    assert attestation_cache.retained_documents == 1
    assert attestation_cache.retained_input_bytes <= ATTESTATION_CACHE_MAX_INPUT_BYTES
    assert attestation_keys[0] in attestation_cache.retained_order
    assert attestation_keys[1] not in attestation_cache.retained_order
    limited_attestation = attestation_cache.values[attestation_keys[1]]
    assert isinstance(limited_attestation, _AttestationMaterial)
    assert limited_attestation.resource_limited is True


def test_artifact_digest_only_cache_rejects_distinct_mismatches_after_one_read() -> None:
    class CountingRepository:
        def __init__(self) -> None:
            self.reads = 0

        def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
            del commit, path, max_bytes
            self.reads += 1
            return b"{}"

    repository = CountingRepository()
    typed_repository = cast(GitRepository, cast(Any, repository))
    cache = _ArtifactCache()
    results = [
        verify_artifact(
            ArtifactEvidence.model_validate(
                {
                    "type": "artifact",
                    "path": "evidence/result.json",
                    "format": "json",
                    "sha256": digit * 64,
                    "checks": [{"pointer": "", "operator": "exists"}],
                }
            ),
            typed_repository,
            "a" * 40,
            1_000,
            cache=cache,
        )
        for digit in ("0", "1")
    ]

    assert [result.code for result in results] == [EvidenceCode.DIGEST_MISMATCH] * 2
    assert repository.reads == 1


def test_artifact_cache_retains_configured_artifact_larger_than_default_limit() -> None:
    content = b'{"padding":"' + (b"x" * 2_000_000) + b'"}'

    class CountingRepository:
        def __init__(self) -> None:
            self.reads = 0

        def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
            del commit, path
            assert len(content) <= max_bytes
            self.reads += 1
            return content

    repository = CountingRepository()
    typed_repository = cast(GitRepository, cast(Any, repository))
    cache = _ArtifactCache()
    for pointer in ("", "/padding"):
        result = verify_artifact(
            ArtifactEvidence.model_validate(
                {
                    "type": "artifact",
                    "path": "evidence/large.json",
                    "format": "json",
                    "checks": [{"pointer": pointer, "operator": "exists"}],
                }
            ),
            typed_repository,
            "a" * 40,
            3_000_000,
            cache=cache,
        )
        assert result.code == EvidenceCode.VERIFIED

    assert repository.reads == 1
    assert cache.retained_documents == 1
    assert cache.retained_input_bytes == len(content)


def test_material_caches_refuse_single_inputs_beyond_absolute_budget() -> None:
    repository = repository_with(b"")
    artifact_cache = _ArtifactCache()
    artifact_key = (repository, "a" * 40, "evidence/result.json", 20_000_000, "json")
    artifact = _ArtifactMaterial(
        "0" * 64,
        parsed=True,
        valid=True,
        document={},
        input_bytes=ARTIFACT_CACHE_MAX_INPUT_BYTES + 1,
    )
    attestation_cache = _AttestationCache()
    attestation_key = (
        repository,
        "b" * 40,
        ".proofstate/attestations/review.json",
        20_000_000,
    )
    attestation = _AttestationMaterial(
        "1" * 64,
        parsed=True,
        input_bytes=ATTESTATION_CACHE_MAX_INPUT_BYTES + 1,
    )

    assert _retain_artifact_material(artifact_cache, artifact_key, artifact) is False
    assert _retain_attestation_material(attestation_cache, attestation_key, attestation) is False
    assert artifact_cache.retained_documents == 0
    assert attestation_cache.retained_documents == 0
    cached_artifact = artifact_cache.values[artifact_key]
    cached_attestation = attestation_cache.values[attestation_key]
    assert isinstance(cached_artifact, _ArtifactMaterial)
    assert isinstance(cached_attestation, _AttestationMaterial)
    assert cached_artifact.resource_limited is True
    assert cached_attestation.resource_limited is True


def test_artifact_git_failure_is_cached_and_fails_closed() -> None:
    cache = _ArtifactCache()
    evidence = ArtifactEvidence.model_validate(
        {
            "type": "artifact",
            "path": "evidence/result.json",
            "format": "json",
            "checks": [{"pointer": "", "operator": "exists"}],
        }
    )
    repository = repository_with(
        ProofStateError(ErrorCode.GIT_COMMAND_FAILED, "synthetic Git failure")
    )

    first = verify_artifact(evidence, repository, "a" * 40, 1_000, cache=cache)
    second = verify_artifact(evidence, repository, "a" * 40, 1_000, cache=cache)

    assert first.code == EvidenceCode.INTERNAL_ERROR
    assert second.code == EvidenceCode.INTERNAL_ERROR
    assert first is not second


def test_attestation_cache_retains_round_robin_documents_within_byte_budget() -> None:
    content = json.dumps(
        {
            "schema_version": "proofstate.dev/attestation/v1alpha1",
            "identity": "reviewer@example.invalid",
            "issued_at": "2025-01-01T00:00:00Z",
            "expires_at": "2027-01-01T00:00:00Z",
            "scope": {
                "repository": "example.invalid/repository",
                "commit": "a" * 40,
                "assertions": ["review"],
            },
            "statement": "Reviewed.",
        },
        separators=(",", ":"),
    ).encode()

    class CountingRepository:
        def __init__(self) -> None:
            self.reads: dict[str, int] = {}

        def read_blob(self, commit: str, path: str, *, max_bytes: int) -> bytes:
            del commit, max_bytes
            self.reads[path] = self.reads.get(path, 0) + 1
            return content

    repository = CountingRepository()
    typed_repository = cast(GitRepository, cast(Any, repository))
    cache = _AttestationCache()
    document_count = 65
    evidences = [
        AttestationEvidence(
            type="human_attestation",
            path=f".proofstate/attestations/review-{index}.json",
        )
        for index in range(document_count)
    ]
    for _ in range(2):
        for evidence in evidences:
            result = verify_attestation(
                evidence,
                typed_repository,
                "b" * 40,
                "a" * 40,
                "example.invalid/repository",
                "review",
                datetime(2026, 1, 1, tzinfo=UTC),
                1_000,
                cache=cache,
            )
            assert result.code == EvidenceCode.VERIFIED

    assert cache.retained_documents == document_count
    assert len(cache.retained_order) == document_count
    assert set(repository.reads.values()) == {1}


def test_attestation_invalid_future_digest_and_missing() -> None:
    evidence = AttestationEvidence(type="human_attestation", path="review.json")
    invalid = verify_attestation(
        evidence,
        repository_with(b"{}"),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        1_000,
    )
    future_document = (
        b'{"schema_version":"proofstate.dev/attestation/v1alpha1",'
        b'"identity":"reviewer@example.invalid",'
        b'"issued_at":"2027-01-01T00:00:00Z",'
        b'"expires_at":"2028-01-01T00:00:00Z",'
        b'"scope":{"repository":"example.invalid/repository",'
        b'"commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"assertions":["review"]},"statement":"Reviewed."}'
    )
    future = verify_attestation(
        evidence,
        repository_with(future_document),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        1_000,
    )
    mismatch = verify_attestation(
        evidence.model_copy(update={"sha256": "0" * 64}),
        repository_with(future_document),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        1_000,
    )
    missing = verify_attestation(
        evidence,
        repository_with(FileNotFoundError()),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        1_000,
    )
    assert invalid.code == EvidenceCode.ATTESTATION_INVALID
    assert future.code == EvidenceCode.ATTESTATION_NOT_YET_VALID
    assert mismatch.code == EvidenceCode.DIGEST_MISMATCH
    assert missing.code == EvidenceCode.FILE_MISSING


def test_yaml_space_separated_attestation_timestamp_fails_closed() -> None:
    evidence = AttestationEvidence(type="human_attestation", path="review.yaml")
    document = b"""\
schema_version: proofstate.dev/attestation/v1alpha1
identity: reviewer@example.invalid
issued_at: 2025-01-01 00:00:00+00:00
expires_at: 2027-01-01T00:00:00Z
scope:
  repository: example.invalid/repository
  commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  assertions:
    - review
statement: Reviewed.
"""

    result = verify_attestation(
        evidence,
        repository_with(document),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        1_000,
    )

    assert result.code == EvidenceCode.ATTESTATION_INVALID


def test_git_failure_in_machine_evidence_fails_closed() -> None:
    result = verify_machine_evidence(
        FileEvidence(type="file", path="file.txt"),
        repository_with(ProofStateError(ErrorCode.GIT_COMMAND_FAILED, "failed")),
        "a" * 40,
        100,
    )
    assert result.code == EvidenceCode.INTERNAL_ERROR


def test_git_failure_in_attestation_fails_closed() -> None:
    result = verify_attestation(
        AttestationEvidence(type="human_attestation", path="review.json"),
        repository_with(ProofStateError(ErrorCode.GIT_COMMAND_FAILED, "failed")),
        "b" * 40,
        "a" * 40,
        "example.invalid/repository",
        "review",
        datetime(2026, 1, 1, tzinfo=UTC),
        100,
    )

    assert result.passed is False
    assert result.code == EvidenceCode.INTERNAL_ERROR
    assert result.path == "review.json"
