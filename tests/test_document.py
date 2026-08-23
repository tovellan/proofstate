from __future__ import annotations

import pytest

from proofstate.document import DocumentError, load_document


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


def test_plain_yaml_is_loaded() -> None:
    assert load_document(b"ready: true\ncount: 3\n") == {"ready": True, "count": 3}
