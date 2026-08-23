"""Bounded parsing for untrusted JSON and YAML documents."""

from __future__ import annotations

import json
import math
import re
from typing import Any, NoReturn

import yaml
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent, ScalarEvent
from yaml.nodes import MappingNode, ScalarNode

MAX_DOCUMENT_DEPTH = 100
MAX_DOCUMENT_NODES = 125_000
MAX_DECIMAL_INTEGER_DIGITS = 4_300


class DocumentError(ValueError):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    def scan_directive(self) -> NoReturn:
        raise DocumentError("YAML directives are not allowed")


UniqueKeyLoader.yaml_implicit_resolvers = {}


_CORE_NULL_PATTERN = re.compile(r"^(?:~|null|Null|NULL|)$")
_CORE_BOOL_PATTERN = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
_CORE_INT_PATTERN = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_CORE_FLOAT_PATTERN = re.compile(
    r"^(?:"
    r"[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)"
    r"|\.(?:nan|NaN|NAN)"
    r")$"
)


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        if isinstance(key_node, ScalarNode) and key_node.value == "<<" and key_node.style is None:
            raise DocumentError("YAML merge keys are not allowed")
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise DocumentError("mapping keys must be strings")
        if key in mapping:
            raise DocumentError(f"duplicate mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


def _construct_decimal_integer(value: str) -> int:
    sign = -1 if value.startswith("-") else 1
    digits = value[1:] if value.startswith(("+", "-")) else value
    if len(digits) > MAX_DECIMAL_INTEGER_DIGITS:
        raise DocumentError(f"decimal integer exceeds the {MAX_DECIMAL_INTEGER_DIGITS} digit limit")
    result = 0
    for offset in range(0, len(digits), 9):
        chunk = digits[offset : offset + 9]
        result = (result * (10 ** len(chunk))) + int(chunk)
    return sign * result


def _construct_core_integer(loader: UniqueKeyLoader, node: ScalarNode) -> int:
    value = loader.construct_scalar(node)
    sign = -1 if value.startswith("-") else 1
    digits = value[1:] if value.startswith(("+", "-")) else value
    if digits.startswith("0o"):
        return sign * int(digits[2:], 8)
    if digits.startswith("0x"):
        return sign * int(digits[2:], 16)
    return _construct_decimal_integer(value)


UniqueKeyLoader.add_implicit_resolver(
    "tag:yaml.org,2002:null",
    _CORE_NULL_PATTERN,
    ["~", "n", "N", ""],
)
UniqueKeyLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    _CORE_BOOL_PATTERN,
    ["t", "T", "f", "F"],
)
UniqueKeyLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    _CORE_INT_PATTERN,
    ["-", "+", *list("0123456789")],
)
UniqueKeyLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    _CORE_FLOAT_PATTERN,
    ["-", "+", ".", *list("0123456789")],
)


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)
UniqueKeyLoader.add_constructor(
    "tag:yaml.org,2002:int",
    _construct_core_integer,
)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DocumentError(f"duplicate mapping key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    raise DocumentError(f"invalid JSON constant: {value}")


def _check_yaml_events(text: str) -> None:
    nodes = 0
    depth = 0
    for event in yaml.parse(text, Loader=UniqueKeyLoader):
        if isinstance(event, AliasEvent):
            raise DocumentError("YAML aliases, anchors, and explicit tags are not allowed")
        if isinstance(event, CollectionEndEvent):
            depth -= 1
        if isinstance(event, (ScalarEvent, CollectionStartEvent)):
            if event.anchor is not None or event.tag is not None:
                raise DocumentError("YAML aliases, anchors, and explicit tags are not allowed")
            if depth > MAX_DOCUMENT_DEPTH:
                raise DocumentError(f"document nesting exceeds {MAX_DOCUMENT_DEPTH} levels")
            nodes += 1
            if nodes > MAX_DOCUMENT_NODES:
                raise DocumentError(f"document exceeds the {MAX_DOCUMENT_NODES} node limit")
            if isinstance(event, CollectionStartEvent):
                depth += 1


def _check_document_depth(document: Any) -> Any:
    pending: list[tuple[Any, int]] = [(document, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > MAX_DOCUMENT_NODES:
            raise DocumentError(f"document exceeds the {MAX_DOCUMENT_NODES} node limit")
        if depth > MAX_DOCUMENT_DEPTH:
            raise DocumentError(f"document nesting exceeds {MAX_DOCUMENT_DEPTH} levels")
        value_type = type(value)
        if value is None or value_type in (bool, int, str):
            continue
        if value_type is float:
            if not math.isfinite(value):
                raise DocumentError("non-finite numbers are not allowed")
        elif value_type is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise DocumentError("mapping keys must be strings")
                pending.append((key, depth + 1))
                pending.append((item, depth + 1))
        elif value_type is list:
            pending.extend((item, depth + 1) for item in value)
        else:
            raise DocumentError("document values must use JSON-compatible types")
    return document


def count_document_nodes(document: Any) -> int:
    """Count nodes in a document already accepted by ``load_document``."""
    pending = [document]
    nodes = 0
    while pending:
        value = pending.pop()
        nodes += 1
        value_type = type(value)
        if value_type is dict:
            for key, item in value.items():
                pending.append(key)
                pending.append(item)
        elif value_type is list:
            pending.extend(value)
    return nodes


def load_document(content: bytes, *, format_hint: str | None = None) -> Any:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentError("document must be valid UTF-8") from error

    if format_hint == "json":
        try:
            return _check_document_depth(
                json.loads(
                    text,
                    object_pairs_hook=_unique_json_object,
                    parse_constant=_reject_json_constant,
                    parse_int=_construct_decimal_integer,
                )
            )
        except (ValueError, RecursionError) as error:
            raise DocumentError(str(error)) from error

    try:
        _check_yaml_events(text)
        # UniqueKeyLoader derives from SafeLoader and cannot construct Python objects.
        return _check_document_depth(yaml.load(text, Loader=UniqueKeyLoader))  # noqa: S506
    except (yaml.YAMLError, ValueError, OverflowError, RecursionError) as error:
        raise DocumentError(str(error)) from error
