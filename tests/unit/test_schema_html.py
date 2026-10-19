import json
from typing import Any

import pytest
from pydantic import ValidationError

from asfb.schema.doc import DocV1, Profile, SourceKind
from asfb.schema.html import Attr, Doctype, Element, Opaque, Text, Url
from asfb.types.token import Token
from asfb.types.values import EnumValue
from tests.stubs import STUB_VOCAB

SAMPLE_ID = "t_0123456789abcdef"
TERM = {"vocab": STUB_VOCAB.id, "term": "btn"}
REF = {"id": STUB_VOCAB.id, "version": 1}
BASE: dict[str, Any] = {
    "profile": "strict",
    "source": "html",
    "vocab": [REF],
    "body": [],
}


def _body(*nodes: dict[str, Any]) -> str:
    return json.dumps({**BASE, "body": list(nodes)})


def _array(item: str, count: int) -> str:
    return "[" + ",".join([item] * count) + "]"


def _element(children: str = "[]", attrs: str = "[]") -> str:
    return (
        '{"kind":"element","tag":{"vocab":"' + STUB_VOCAB.id + '","term":"btn"},'
        '"attrs":' + attrs + ',"children":' + children + "}"
    )


TEXT = '{"kind":"text","text":"' + SAMPLE_ID + '"}'


def test_nested_tree_validates_and_round_trips() -> None:
    raw = _body(
        {"kind": "doctype", "html": True},
        {"kind": "opaque", "form": "comment", "text": SAMPLE_ID},
        {
            "kind": "element",
            "tag": TERM,
            "attrs": [{"name": TERM, "value": True}],
            "children": [
                {"kind": "text", "text": SAMPLE_ID},
                {"kind": "element", "tag": TERM, "children": [{"kind": "text", "text": SAMPLE_ID}]},
            ],
        },
    )
    doc = DocV1.model_validate_json(raw, strict=True)
    assert doc == DocV1.model_validate_json(doc.model_dump_json(), strict=True)
    outer = doc.body[2]
    assert isinstance(outer, Element)
    inner = outer.children[1]
    assert isinstance(inner, Element)
    assert isinstance(inner.children[0], Text)


@pytest.mark.parametrize(
    "node",
    [
        {"kind": "text", "tag": TERM, "attrs": []},
        {"kind": "element", "text": SAMPLE_ID},
        {"kind": "opaque", "text": SAMPLE_ID},
        {"kind": "doctype", "text": SAMPLE_ID},
        {"text": SAMPLE_ID},
        {"tag": TERM},
        {"kind": ["text"], "text": SAMPLE_ID},
        {"kind": "Text", "text": SAMPLE_ID},
        {"kind": "script", "text": SAMPLE_ID},
    ],
)
def test_discriminator_rejects_wrong_or_missing_kind(node: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DocV1.model_validate_json(_body(node), strict=True)


def test_discriminator_accepts_each_kind() -> None:
    raw = _body(
        {"kind": "element", "tag": TERM},
        {"kind": "text", "text": SAMPLE_ID},
        {"kind": "opaque", "form": "cdata", "text": SAMPLE_ID},
        {"kind": "doctype", "html": False},
    )
    doc = DocV1.model_validate_json(raw, strict=True)
    assert [type(node) for node in doc.body] == [Element, Text, Opaque, Doctype]


def test_doc_source_must_be_html() -> None:
    assert DocV1.model_validate_json(json.dumps(BASE), strict=True).source is SourceKind.HTML
    with pytest.raises(ValidationError):
        DocV1.model_validate_json(json.dumps({**BASE, "source": "xml"}), strict=True)


def test_python_mode_builds_nested_tree() -> None:
    term = EnumValue(vocab=STUB_VOCAB.id, term="btn")
    token = Token(SAMPLE_ID)
    doc = DocV1(
        profile=Profile.STRICT,
        source=SourceKind.HTML,
        vocab=(),
        body=(Element(tag=term, children=(Text(text=token),)),),
    )
    assert doc.body == (Element(tag=term, children=(Text(text=token),)),)


@pytest.mark.parametrize(
    ("count", "accepted"),
    [(20000, True), (20001, False)],
)
def test_children_bound(count: int, accepted: bool) -> None:
    raw = _element(children=_array(TEXT, count))
    if accepted:
        assert len(Element.model_validate_json(raw, strict=True).children) == count
    else:
        with pytest.raises(ValidationError):
            Element.model_validate_json(raw, strict=True)


@pytest.mark.parametrize(("count", "accepted"), [(64, True), (65, False)])
def test_attrs_bound(count: int, accepted: bool) -> None:
    attr = '{"name":' + json.dumps(TERM) + ',"value":true}'
    raw = _element(attrs=_array(attr, count))
    if accepted:
        assert len(Element.model_validate_json(raw, strict=True).attrs) == count
    else:
        with pytest.raises(ValidationError):
            Element.model_validate_json(raw, strict=True)


@pytest.mark.parametrize(("count", "accepted"), [(256, True), (257, False)])
def test_url_path_bound(count: int, accepted: bool) -> None:
    raw = '{"path":' + _array(f'"{SAMPLE_ID}"', count) + "}"
    if accepted:
        assert len(Url.model_validate_json(raw, strict=True).path) == count
    else:
        with pytest.raises(ValidationError):
            Url.model_validate_json(raw, strict=True)


@pytest.mark.parametrize(("count", "accepted"), [(128, True), (129, False)])
def test_attr_list_value_bound(count: int, accepted: bool) -> None:
    attr = '{"name":' + json.dumps(TERM) + ',"value":' + _array(f'"{SAMPLE_ID}"', count) + "}"
    if accepted:
        assert Attr.model_validate_json(attr, strict=True).value is not None
    else:
        with pytest.raises(ValidationError):
            Attr.model_validate_json(attr, strict=True)


@pytest.mark.parametrize(("count", "accepted"), [(20000, True), (20001, False)])
def test_body_bound(count: int, accepted: bool) -> None:
    raw = json.dumps({**BASE, "body": json.loads(_array(TEXT, count))})
    if accepted:
        assert len(DocV1.model_validate_json(raw, strict=True).body) == count
    else:
        with pytest.raises(ValidationError):
            DocV1.model_validate_json(raw, strict=True)


def test_url_shape_accepts_full_and_empty_forms() -> None:
    full = {
        "scheme": TERM,
        "authority": SAMPLE_ID,
        "rooted": True,
        "path": [SAMPLE_ID, SAMPLE_ID],
        "trailing_slash": True,
        "query": SAMPLE_ID,
        "fragment": SAMPLE_ID,
    }
    url = Url.model_validate_json(json.dumps(full), strict=True)
    assert url.scheme == EnumValue(vocab=STUB_VOCAB.id, term="btn")
    assert url.path == (SAMPLE_ID, SAMPLE_ID)
    assert Url.model_validate_json("{}", strict=True) == Url()


@pytest.mark.parametrize(
    "payload",
    [
        {"scheme": "https"},
        {"authority": "example.com"},
        {"path": ["a"]},
        {"rooted": 1},
        {"trailing_slash": "yes"},
        {"query": "a=b"},
        {"bogus": SAMPLE_ID},
    ],
)
def test_url_shape_rejects_free_values(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Url.model_validate_json(json.dumps(payload), strict=True)


@pytest.mark.parametrize(
    ("value", "expected_type"),
    [
        ("true", bool),
        (f'"{SAMPLE_ID}"', Token),
        (json.dumps(TERM), EnumValue),
        ('{"scheme":' + json.dumps(TERM) + "}", Url),
        ("[" + json.dumps(TERM) + f',"{SAMPLE_ID}"]', tuple),
        ("[]", tuple),
    ],
)
def test_attr_value_variants(value: str, expected_type: type) -> None:
    attr = Attr.model_validate_json('{"name":' + json.dumps(TERM) + ',"value":' + value + "}")
    assert type(attr.value) is expected_type


@pytest.mark.parametrize(
    "value",
    ["false", "1", "null", '"hello"', '"t_0123456789ABCDEF"', '[{"scheme":"x"}]', "[[]]"],
)
def test_attr_value_rejects_other_shapes(value: str) -> None:
    with pytest.raises(ValidationError):
        Attr.model_validate_json('{"name":' + json.dumps(TERM) + ',"value":' + value + "}")


def test_literal_true_does_not_accept_false_in_python_mode() -> None:
    name = EnumValue(vocab=STUB_VOCAB.id, term="btn")
    with pytest.raises(ValidationError):
        Attr.model_validate({"name": name, "value": False})
    assert Attr.model_validate({"name": name, "value": True}).value is True


def test_opaque_form_and_doctype_shapes() -> None:
    assert Doctype.model_validate_json('{"html":false}', strict=True).html is False
    with pytest.raises(ValidationError):
        Opaque.model_validate_json(f'{{"form":"script","text":"{SAMPLE_ID}"}}', strict=True)
    with pytest.raises(ValidationError):
        Doctype.model_validate_json('{"html":"true"}', strict=True)
