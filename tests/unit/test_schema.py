import json
from collections.abc import Iterator
from importlib import resources
from typing import Any

import pytest
from pydantic import ValidationError

from asfb.schema import SCHEMA_RESOURCE
from asfb.schema.doc import DocV1, Profile

BASE: dict[str, Any] = {"profile": "strict", "vocab": [], "items": []}
SAMPLE_ID = "t_0123456789abcdef"


def _parse(payload: dict[str, Any]) -> DocV1:
    return DocV1.model_validate_json(json.dumps(payload), strict=True)


def _committed() -> str:
    return resources.files("asfb.schema").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")


def _nodes(node: object) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _nodes(item)


def test_committed_schema_equals_model() -> None:
    assert json.loads(_committed()) == DocV1.model_json_schema()


def test_committed_schema_bytes_are_canonical() -> None:
    expected = json.dumps(DocV1.model_json_schema(), indent=2, sort_keys=True) + "\n"
    assert _committed() == expected


def test_schema_walk_is_closed() -> None:
    strings = objects = integers = 0
    for node in _nodes(json.loads(_committed())):
        kinds = node.get("type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        assert "number" not in kinds, node
        if "string" in kinds:
            strings += 1
            assert {"pattern", "const", "enum"} & node.keys(), node
        if "object" in kinds or "properties" in node:
            objects += 1
            assert node.get("additionalProperties") is False, node
        if "integer" in kinds:
            integers += 1
            assert "minimum" in node, node
            assert "maximum" in node, node
    assert strings > 0
    assert objects > 0
    assert integers > 0


def test_accepts_base_document() -> None:
    assert _parse(BASE) == DocV1(profile=Profile.STRICT, vocab=(), items=())


def test_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _parse({**BASE, "extra": 1})


@pytest.mark.parametrize("schema_id", ["asfb.doc/2", "asfb.doc/1 "])
def test_rejects_other_schema_ids(schema_id: str) -> None:
    with pytest.raises(ValidationError):
        _parse({**BASE, "schema_id": schema_id})


@pytest.mark.parametrize(
    "payload",
    [
        {**BASE, "items": ["hello"]},
        {**BASE, "items": ["t_0123456789ABCDEF"]},
        {**BASE, "items": [1.0]},
        {**BASE, "items": [2**31]},
        {**BASE, "items": [-1]},
        {**BASE, "items": [None]},
        {**BASE, "items": ["1"]},
        {**BASE, "profile": "STRICT"},
        {**BASE, "vocab": [{"id": "test.stub", "version": "1"}]},
        {**BASE, "vocab": [{"id": "test.stub", "version": 0}]},
        {**BASE, "vocab": [{"id": "test.stub", "version": 65536}]},
        {**BASE, "vocab": [{"id": "test.stub", "version": True}]},
        {**BASE, "items": [{"scheme": "log2", "index": True}]},
        {**BASE, "items": [{"scheme": "log2", "index": 65}]},
    ],
)
def test_rejects_free_values_and_coercion(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _parse(payload)


def test_accepts_token_and_bool_and_int_leaves_from_json() -> None:
    raw = json.dumps({**BASE, "items": [SAMPLE_ID, True, False, 0, 7]})
    doc = DocV1.model_validate_json(raw, strict=True)
    assert doc.items == (SAMPLE_ID, True, False, 0, 7)
    assert type(doc.items[1]) is bool
    assert type(doc.items[3]) is int


def test_python_mode_requires_tuples_for_collections() -> None:
    lists: dict[str, Any] = {"profile": Profile.STRICT, "vocab": [], "items": []}
    with pytest.raises(ValidationError) as info:
        DocV1.model_validate(lists)
    assert {error["loc"] for error in info.value.errors()} == {("vocab",), ("items",)}
    doc = DocV1.model_validate({"profile": Profile.STRICT, "vocab": (), "items": ()})
    assert doc.vocab == ()
    assert doc.items == ()
