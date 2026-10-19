import json
from collections.abc import Iterator
from importlib import resources
from typing import Any

import pytest
from pydantic import ValidationError

from asfb.schema import SCHEMA_RESOURCE
from asfb.schema.doc import DocV1, Profile, SourceKind

BASE: dict[str, Any] = {"profile": "strict", "source": "html", "vocab": [], "body": []}
SAMPLE_ID = "t_0123456789abcdef"
JSON_TYPES = frozenset({"string", "object", "integer", "boolean", "array", "null"})
DEF_PREFIX = "#/$defs/"


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
    schema = json.loads(_committed())
    defs = schema["$defs"]
    strings = objects = integers = refs = 0
    for node in _nodes(schema):
        kinds = node.get("type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        assert set(kinds) <= JSON_TYPES, node
        assert "number" not in kinds, node
        if "$ref" in node:
            refs += 1
            assert node["$ref"].startswith(DEF_PREFIX), node
            assert node["$ref"].removeprefix(DEF_PREFIX) in defs, node
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
    assert refs > 0


def test_accepts_base_document() -> None:
    assert _parse(BASE) == DocV1(profile=Profile.STRICT, source=SourceKind.HTML, vocab=(), body=())


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
        {**BASE, "body": ["hello"]},
        {**BASE, "body": [SAMPLE_ID]},
        {**BASE, "body": [1.0]},
        {**BASE, "body": [2**31]},
        {**BASE, "body": [-1]},
        {**BASE, "body": [None]},
        {**BASE, "body": [True]},
        {**BASE, "body": ["1"]},
        {**BASE, "body": [{"kind": "text", "text": "t_0123456789ABCDEF"}]},
        {**BASE, "body": [{"kind": "text", "text": "hello"}]},
        {**BASE, "body": [{"kind": "text", "text": SAMPLE_ID, "extra": 1}]},
        {**BASE, "body": [{"kind": "script", "text": SAMPLE_ID}]},
        {**BASE, "items": []},
        {**BASE, "profile": "STRICT"},
        {**BASE, "source": "htm"},
        {**BASE, "source": None},
        {**BASE, "vocab": [{"id": "test.stub", "version": "1"}]},
        {**BASE, "vocab": [{"id": "test.stub", "version": 0}]},
        {**BASE, "vocab": [{"id": "test.stub", "version": 65536}]},
        {**BASE, "vocab": [{"id": "test.stub", "version": True}]},
    ],
)
def test_rejects_free_values_and_coercion(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _parse(payload)


def test_missing_source_is_rejected() -> None:
    payload = {key: value for key, value in BASE.items() if key != "source"}
    with pytest.raises(ValidationError):
        _parse(payload)


def test_accepts_text_and_element_nodes_from_json() -> None:
    raw = json.dumps(
        {
            **BASE,
            "body": [
                {"kind": "text", "text": SAMPLE_ID},
                {
                    "kind": "element",
                    "tag": {"vocab": "test.stub", "term": "btn"},
                    "attrs": [],
                    "children": [{"kind": "text", "text": SAMPLE_ID}],
                },
            ],
        }
    )
    doc = DocV1.model_validate_json(raw, strict=True)
    assert len(doc.body) == 2
    assert doc.body[0].kind == "text"
    assert doc.body[1].kind == "element"


def test_python_mode_requires_tuples_for_collections() -> None:
    lists: dict[str, Any] = {
        "profile": Profile.STRICT,
        "source": SourceKind.HTML,
        "vocab": [],
        "body": [],
    }
    with pytest.raises(ValidationError) as info:
        DocV1.model_validate(lists)
    assert {error["loc"] for error in info.value.errors()} == {("vocab",), ("body",)}
    doc = DocV1.model_validate(
        {"profile": Profile.STRICT, "source": SourceKind.HTML, "vocab": (), "body": ()}
    )
    assert doc.vocab == ()
    assert doc.body == ()
