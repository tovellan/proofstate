from __future__ import annotations

import sys

import pytest
import yaml

import proofstate.document as document_module
from proofstate.document import (
    MAX_DECIMAL_INTEGER_DIGITS,
    DocumentError,
    _check_document_depth,
    load_document,
)


@pytest.mark.parametrize(
    "content",
    [
        b'{"key": 1, "key": 2}',
        b"key: 1\nkey: 2\n",
        b"base: &base\n  enabled: true\ncopy: *base\n",
        b"value: !!python/object:example.Type {}\n",
        b"\xff",
    ],
)
def test_ambiguous_or_unsafe_documents_are_rejected(content: bytes) -> None:
    with pytest.raises(DocumentError):
        load_document(content)


def test_json_duplicate_keys_are_rejected_with_json_hint() -> None:
    with pytest.raises(DocumentError):
        load_document(b'{"key": 1, "key": 2}', format_hint="json")


def test_out_of_range_yaml_unicode_escape_is_rejected() -> None:
    with pytest.raises(DocumentError):
        load_document(b'"\\UFFFFFFFF"')


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_nonstandard_json_constants_are_rejected(constant: bytes) -> None:
    with pytest.raises(DocumentError):
        load_document(b'{"value": ' + constant + b"}", format_hint="json")


def test_overflowing_json_number_is_rejected() -> None:
    with pytest.raises(DocumentError, match="non-finite"):
        load_document(b'{"value": 1e400}', format_hint="json")


@pytest.mark.parametrize(
    "scalar",
    [
        ".inf",
        ".Inf",
        ".INF",
        "+.inf",
        "+.Inf",
        "+.INF",
        "-.inf",
        "-.Inf",
        "-.INF",
        ".nan",
        ".NaN",
        ".NAN",
        "1e400",
        "-1e400",
    ],
)
def test_non_finite_yaml_numbers_are_rejected(scalar: str) -> None:
    with pytest.raises(DocumentError):
        load_document(f"value: {scalar}\n".encode())


@pytest.mark.parametrize(
    "content",
    [
        b"outer:\n  .inf: value\n",
        b"outer:\n  1: value\n",
        b"outer:\n  true: value\n",
        b"outer:\n  null: value\n",
    ],
)
def test_non_json_yaml_mapping_keys_are_rejected(content: bytes) -> None:
    with pytest.raises(DocumentError):
        load_document(content)


def test_yaml_timestamp_scalars_remain_strings() -> None:
    assert load_document(b"value: 2026-01-01 00:00:00+00:00\n") == {
        "value": "2026-01-01 00:00:00+00:00"
    }


def test_yaml_core_scalar_values_are_loaded_exactly() -> None:
    document = load_document(
        b"decimal: 08\n"
        b"leading_zero: 012\n"
        b"octal: 0o12\n"
        b"hexadecimal: 0x3A\n"
        b"exponent: 1e3\n"
        b"leading_dot: .5\n"
        b"trailing_dot: 1.\n"
    )

    assert document == {
        "decimal": 8,
        "leading_zero": 12,
        "octal": 10,
        "hexadecimal": 58,
        "exponent": 1000.0,
        "leading_dot": 0.5,
        "trailing_dot": 1.0,
    }
    assert type(document["exponent"]) is float


def test_yaml_legacy_numeric_forms_remain_strings() -> None:
    assert load_document(b"binary: 0b10\nsexagesimal: 1:20\nseparator: 1_000\n") == {
        "binary": "0b10",
        "sexagesimal": "1:20",
        "separator": "1_000",
    }


def test_yaml_core_integer_signs_apply_only_to_decimals() -> None:
    document = load_document(
        b"positive_decimal: +7\n"
        b"negative_decimal: -7\n"
        b"positive_octal: +0o7\n"
        b"negative_octal: -0o7\n"
        b"positive_hexadecimal: +0xA\n"
        b"negative_hexadecimal: -0xA\n"
    )

    assert document == {
        "positive_decimal": 7,
        "negative_decimal": -7,
        "positive_octal": "+0o7",
        "negative_octal": "-0o7",
        "positive_hexadecimal": "+0xA",
        "negative_hexadecimal": "-0xA",
    }
    assert type(document["positive_decimal"]) is int
    assert type(document["negative_decimal"]) is int


def test_yaml_core_boolean_and_null_variants_are_loaded_exactly() -> None:
    assert load_document(
        b"true_lower: true\n"
        b"true_title: True\n"
        b"true_upper: TRUE\n"
        b"false_lower: false\n"
        b"false_title: False\n"
        b"false_upper: FALSE\n"
        b"null_lower: null\n"
        b"null_title: Null\n"
        b"null_upper: NULL\n"
        b"null_tilde: ~\n"
        b"null_empty:\n"
        b"yes: yes\n"
        b"no: no\n"
        b"on: on\n"
        b"off: off\n"
    ) == {
        "true_lower": True,
        "true_title": True,
        "true_upper": True,
        "false_lower": False,
        "false_title": False,
        "false_upper": False,
        "null_lower": None,
        "null_title": None,
        "null_upper": None,
        "null_tilde": None,
        "null_empty": None,
        "yes": "yes",
        "no": "no",
        "on": "on",
        "off": "off",
    }


@pytest.mark.parametrize(
    "content",
    [
        b"copy:\n  <<: {ready: true}\n",
        b"copy: {<<: {ready: true}}\n",
        b"outer:\n  nested:\n    <<: {ready: true}\n",
    ],
)
def test_yaml_merge_keys_are_rejected(content: bytes) -> None:
    with pytest.raises(DocumentError, match="merge keys"):
        load_document(content)


def test_quoted_merge_key_and_plain_merge_value_remain_strings() -> None:
    assert load_document(b"'<<': key\nvalue: <<\n") == {"<<": "key", "value": "<<"}


def test_project_yaml_resolvers_do_not_modify_pyyaml_safe_load() -> None:
    assert load_document(b"value: yes\n") == {"value": "yes"}
    assert yaml.safe_load("value: yes\n") == {"value": True}


def test_yaml_node_limit_counts_containers_keys_and_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_module, "MAX_DOCUMENT_NODES", 3)

    assert load_document(b"value: 0\n") == {"value": 0}
    monkeypatch.setattr(
        yaml,
        "load",
        lambda *_args, **_kwargs: pytest.fail("over-limit YAML was composed"),
    )
    with pytest.raises(DocumentError, match="3 node limit"):
        load_document(b"value: [0]\n")
    assert load_document(b'{"value":0}', format_hint="json") == {"value": 0}
    with pytest.raises(DocumentError, match="3 node limit"):
        load_document(b'{"value":[0]}', format_hint="json")


@pytest.mark.parametrize("value", [b"bytes", ("tuple",), {"set"}])
def test_non_json_python_values_are_rejected(value: object) -> None:
    with pytest.raises(DocumentError, match="JSON-compatible"):
        _check_document_depth({"nested": [value]})


def test_decimal_integer_limit_is_independent_of_process_global_setting() -> None:
    original_limit = sys.get_int_max_str_digits()
    accepted = b"9" * MAX_DECIMAL_INTEGER_DIGITS
    rejected = accepted + b"9"
    try:
        sys.set_int_max_str_digits(640)
        assert type(load_document(accepted, format_hint="json")) is int
        assert type(load_document(accepted)) is int

        sys.set_int_max_str_digits(0)
        with pytest.raises(DocumentError, match="4300 digit limit"):
            load_document(rejected, format_hint="json")
        with pytest.raises(DocumentError, match="4300 digit limit"):
            load_document(rejected)
    finally:
        sys.set_int_max_str_digits(original_limit)


def test_yaml_directive_numbers_do_not_use_process_global_integer_conversion() -> None:
    original_limit = sys.get_int_max_str_digits()
    content = b"%YAML 1." + (b"9" * 5_000) + b"\n---\nvalue: 1\n"
    try:
        for limit in (640, 0):
            sys.set_int_max_str_digits(limit)
            with pytest.raises(DocumentError, match="directives are not allowed"):
                load_document(content)
    finally:
        sys.set_int_max_str_digits(original_limit)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b'"first\n%second"\n', "first %second"),
        (b"'first\n%second'\n", "first %second"),
        (b'["first\n%second"]\n', ["first %second"]),
        (b'key: "first\n%second"\n', {"key": "first %second"}),
    ],
)
def test_percent_continuation_inside_flow_scalar_is_not_a_directive(
    content: bytes,
    expected: object,
) -> None:
    assert load_document(content) == expected


def test_excessively_nested_json_is_rejected() -> None:
    content = ("[" * 2_000 + "0" + "]" * 2_000).encode()
    with pytest.raises(DocumentError):
        load_document(content, format_hint="json")


def test_excessively_nested_yaml_is_rejected_before_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = ("[" * 102 + "0" + "]" * 102).encode()
    monkeypatch.setattr(
        yaml,
        "load",
        lambda *_args, **_kwargs: pytest.fail("over-depth YAML was composed"),
    )

    with pytest.raises(DocumentError, match="nesting exceeds"):
        load_document(content)


def test_plain_yaml_is_loaded() -> None:
    assert load_document(b"ready: true\ncount: 3\n") == {"ready": True, "count": 3}
