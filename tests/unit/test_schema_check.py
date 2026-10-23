"""The compiled checker agrees with jsonschema on every committed schema, and refuses what it does
not compile. The differential runs use random JSON and mutated emitted documents."""

import json
from collections.abc import Callable
from importlib import resources
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from asfb import api
from asfb.egress import emit
from asfb.egress.schema_check import SchemaError, compile_schema
from asfb.ingest import log as ingest_log
from asfb.sanitize.log import censor_log_with_pairs
from asfb.schema import (
    CODE_RESOURCE,
    DIFF_RESOURCE,
    LIST_RESOURCE,
    LOG_RESOURCE,
    MD_RESOURCE,
    RUN_RESOURCE,
    SCHEMA_RESOURCE,
    TEXT_RESOURCE,
)
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.vocab import registry

RESOURCES = (
    SCHEMA_RESOURCE,
    LIST_RESOURCE,
    DIFF_RESOURCE,
    RUN_RESOURCE,
    TEXT_RESOURCE,
    MD_RESOURCE,
    CODE_RESOURCE,
    LOG_RESOURCE,
)


def _load(resource: str) -> dict[str, Any]:
    text = resources.files("asfb.schema").joinpath(resource).read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


SCHEMAS = {resource: _load(resource) for resource in RESOURCES}

WORDS = st.sampled_from(
    ["", "t_0123456789abcdef", "t_0123456789abcdeg", "python", "asfb.code/1", "0", "1", "py"]
)
KEYS = st.sampled_from(
    ["kind", "value", "line", "depth", "prefix", "schema_id", "vocab", "body", "x", "lexemes"]
)
LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-3, max_value=120_000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    WORDS,
)
JSON_VALUES = st.recursive(
    LEAVES,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(KEYS, children, max_size=4),
    ),
    max_leaves=25,
)


def _jsonschema_valid(schema: dict[str, Any], instance: object) -> bool:
    return Draft202012Validator(schema).is_valid(instance)


@pytest.mark.parametrize("resource", RESOURCES)
def test_committed_schema_compiles(resource: str) -> None:
    compile_schema(SCHEMAS[resource])


@pytest.mark.parametrize("resource", RESOURCES)
@settings(max_examples=150, deadline=None)
@given(instance=JSON_VALUES)
def test_random_json_agrees_with_jsonschema(resource: str, instance: object) -> None:
    schema = SCHEMAS[resource]
    assert compile_schema(schema)(instance) is _jsonschema_valid(schema, instance)


Draw = Callable[[st.SearchStrategy[Any]], Any]


def _mutate(draw: Draw, node: Any) -> Any:
    """A copy of node with one random change: a value replaced, a key dropped or added."""
    if isinstance(node, dict) and node:
        key = draw(st.sampled_from(sorted(node)))
        choice = draw(st.integers(min_value=0, max_value=2))
        copy = dict(node)
        if choice == 0:
            del copy[key]
        elif choice == 1:
            copy[key] = _mutate(draw, copy[key])
        else:
            copy[draw(KEYS)] = draw(JSON_VALUES)
        return copy
    if isinstance(node, list) and node:
        index = draw(st.integers(min_value=0, max_value=len(node) - 1))
        copy_list = list(node)
        copy_list[index] = _mutate(draw, copy_list[index])
        return copy_list
    return draw(JSON_VALUES)


@pytest.mark.parametrize(
    "resource", [SCHEMA_RESOURCE, TEXT_RESOURCE, MD_RESOURCE, CODE_RESOURCE, LOG_RESOURCE]
)
@settings(max_examples=120, deadline=None)
@given(data=st.data())
def test_mutated_emitted_documents_agree_with_jsonschema(
    resource: str, data: st.DataObject
) -> None:
    schema = SCHEMAS[resource]
    instance = _valid_base(resource)
    for _ in range(data.draw(st.integers(min_value=1, max_value=3))):
        instance = _mutate(data.draw, instance)
    assert compile_schema(schema)(instance) is _jsonschema_valid(schema, instance)


_SAMPLES = {
    SCHEMA_RESOURCE: "<p>hello <b>world</b></p>",
    TEXT_RESOURCE: "first line\nsecond line\n",
    MD_RESOURCE: "# Title\n\nSome *text* here.\n",
    CODE_RESOURCE: "def f(x):\n    return x  # note\n",
    LOG_RESOURCE: "2024-05-01T10:00:00Z WARN app.db: slow\n2024-05-01T10:00:01Z INFO done\n",
}


def _valid_base(resource: str) -> Any:
    """A document emitted by the public API, the base of the mutations."""
    if resource == SCHEMA_RESOURCE:
        return json.loads(api.sanitize_html(_SAMPLES[SCHEMA_RESOURCE]).json)
    if resource == TEXT_RESOURCE:
        return json.loads(api.sanitize_text(_SAMPLES[TEXT_RESOURCE]).json)
    if resource == MD_RESOURCE:
        return json.loads(api.sanitize_markdown(_SAMPLES[MD_RESOURCE]).json)
    if resource == CODE_RESOURCE:
        return json.loads(api.sanitize_python(_SAMPLES[CODE_RESOURCE]).json)
    return _log_base()


def _log_base() -> Any:
    """A log document emitted through the same stages as the public API (no string entry)."""
    key = SessionKey(bytes(range(32)))
    model, _pairs = censor_log_with_pairs(
        key, ingest_log.read_text(_SAMPLES[LOG_RESOURCE], "text"), Profile.STRICT
    )
    return json.loads(emit(model, vocab=registry()))


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({"type": "string", "prefixItems": []}, id="prefix-items"),
        pytest.param({"$ref": "https://example.com/x.json"}, id="remote-ref"),
        pytest.param({"$ref": "#/$defs/Missing", "$defs": {}}, id="missing-def"),
        pytest.param({"items": [{"type": "string"}]}, id="tuple-items"),
        pytest.param({"enum": [1.5]}, id="float-enum"),
        pytest.param({"enum": [{"a": 1}]}, id="object-enum"),
        pytest.param({"type": "uuid"}, id="unknown-type"),
        pytest.param({"pattern": "("}, id="bad-pattern"),
        pytest.param({"minLength": -1}, id="negative-bound"),
        pytest.param({"anyOf": []}, id="empty-any-of"),
        pytest.param({"patternProperties": {}}, id="pattern-properties"),
    ],
)
def test_constructs_outside_the_subset_are_refused(schema: dict[str, Any]) -> None:
    with pytest.raises(SchemaError):
        compile_schema(schema)


def test_annotations_are_ignored_like_jsonschema() -> None:
    schema = {
        "title": "T",
        "description": "d",
        "default": 1,
        "discriminator": {"x": 1},
        "type": "string",
    }
    assert compile_schema(schema)("a") is True
    assert compile_schema(schema)(1) is False


def test_booleans_are_not_integers_or_const_ones() -> None:
    assert compile_schema({"type": "integer"})(True) is False
    assert compile_schema({"type": "integer"})(1) is True
    assert compile_schema({"const": True})(1) is False
    assert compile_schema({"const": True})(True) is True
    assert compile_schema({"enum": ["0", "1"]})(0) is False
    assert compile_schema({"enum": ["0", "1"]})("1") is True


def test_pattern_searches_and_checks_only_strings() -> None:
    check = compile_schema({"pattern": "^t_[0-9a-f]{4}$"})
    assert check("t_00ff") is True
    assert check("xt_00ff") is False
    assert check(5) is True


def test_numeric_and_size_bounds_apply_only_to_their_types() -> None:
    check = compile_schema({"minimum": 0, "maximum": 3, "maxLength": 2, "maxItems": 1})
    assert check(3) is True
    assert check(4) is False
    assert check(True) is True
    assert check("abc") is False
    assert check([1, 2]) is False


def test_additional_properties_false_and_property_names() -> None:
    closed = compile_schema(
        {"properties": {"a": {"type": "integer"}}, "additionalProperties": False}
    )
    assert closed({"a": 1}) is True
    assert closed({"a": 1, "b": 2}) is False
    names = compile_schema({"propertyNames": {"pattern": "^[a-z]+$"}})
    assert names({"ab": 1}) is True
    assert names({"A": 1}) is False


def test_one_of_needs_exactly_one_branch() -> None:
    check = compile_schema({"oneOf": [{"type": "integer"}, {"minimum": 0}]})
    assert check(5) is False  # both branches hold
    assert check("x") is True  # only the second branch applies to strings
    assert check(-1) is True


def test_recursive_definitions_agree_with_jsonschema() -> None:
    schema = {
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"next": {"anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}]}},
                "additionalProperties": False,
            }
        },
        "$ref": "#/$defs/Node",
    }
    check = compile_schema(schema)
    for instance in ({"next": None}, {"next": {"next": {"next": None}}}, {"next": {"x": 1}}, 3):
        assert check(instance) is _jsonschema_valid(schema, instance)
