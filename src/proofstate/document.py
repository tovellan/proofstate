"""Bounded parsing for untrusted JSON and YAML documents."""

from __future__ import annotations

import json
from collections.abc import Hashable
from typing import Any, NoReturn

import yaml
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

MAX_DOCUMENT_DEPTH = 100


class DocumentError(ValueError):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Hashable, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Hashable, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise DocumentError("mapping keys must be hashable")
        if key in mapping:
            raise DocumentError(f"duplicate mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
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


def _check_document_depth(document: Any) -> Any:
    pending: list[tuple[Any, int]] = [(document, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > MAX_DOCUMENT_DEPTH:
            raise DocumentError(f"document nesting exceeds {MAX_DOCUMENT_DEPTH} levels")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return document


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
                )
            )
        except (json.JSONDecodeError, DocumentError, RecursionError) as error:
            raise DocumentError(str(error)) from error

    try:
        for token in yaml.scan(text, Loader=UniqueKeyLoader):
            if isinstance(token, (AliasToken, AnchorToken, TagToken)):
                raise DocumentError("YAML aliases, anchors, and explicit tags are not allowed")
        # UniqueKeyLoader derives from SafeLoader and cannot construct Python objects.
        return _check_document_depth(yaml.load(text, Loader=UniqueKeyLoader))  # noqa: S506
    except (yaml.YAMLError, DocumentError, RecursionError) as error:
        raise DocumentError(str(error)) from error
