import json
from typing import Any, cast

import pytest
from pydantic import SerializerFunctionWrapHandler, model_serializer

from asfb.egress import gate
from asfb.egress.gate import emit
from asfb.errors import EgressError, ErrorCode
from asfb.schema.doc import DocV1, Profile, SourceKind, VocabRef
from asfb.schema.html import Attr, Element, Node, Text
from asfb.types.token import Token
from asfb.types.values import EnumValue
from tests.stubs import STUB_VOCAB

TOKEN = Token("t_0123456789abcdef")
STUB_REF = VocabRef(id=STUB_VOCAB.id, version=1)
BTN = EnumValue(vocab=STUB_VOCAB.id, term="btn")
FORM = EnumValue(vocab=STUB_VOCAB.id, term="form")
REGISTRY = {STUB_VOCAB.id: STUB_VOCAB}
DOC = DocV1(
    profile=Profile.STRICT,
    source=SourceKind.HTML,
    vocab=(STUB_REF,),
    body=(
        Text(text=TOKEN),
        Element(tag=BTN, attrs=(Attr(name=FORM, value=True),), children=(Text(text=TOKEN),)),
    ),
)


def _doc(*, vocab: tuple[VocabRef, ...] = (STUB_REF,), body: tuple[Node, ...] = ()) -> DocV1:
    return DocV1(profile=Profile.STRICT, source=SourceKind.HTML, vocab=vocab, body=body)


class _SubDoc(DocV1):
    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {
            "schema_id": "asfb.doc/1",
            "profile": "strict",
            "source": "html",
            "vocab": [],
            "body": [],
        }


class _RejectAll:
    def is_valid(self, instance: object) -> bool:
        return False


def _raw(body: tuple[Any, ...]) -> DocV1:
    return DocV1.model_construct(
        profile=Profile.STRICT, source=SourceKind.HTML, vocab=(STUB_REF,), body=body
    )


def _assert_clean(error: EgressError, code: ErrorCode) -> None:
    assert error.code is code
    assert error.args == (code.value,)
    assert str(error) == code.value
    assert repr(error) == f"EgressError('{code.value}')"
    assert error.__cause__ is None
    assert error.__context__ is None


def test_emits_canonical_json_for_valid_document() -> None:
    out = emit(DOC, vocab=REGISTRY)
    assert json.loads(out) == {
        "schema_id": "asfb.doc/1",
        "profile": "strict",
        "source": "html",
        "vocab": [{"id": "test.stub", "version": 1}],
        "body": [
            {"kind": "text", "text": "t_0123456789abcdef"},
            {
                "kind": "element",
                "tag": {"vocab": "test.stub", "term": "btn"},
                "attrs": [{"name": {"vocab": "test.stub", "term": "form"}, "value": True}],
                "children": [{"kind": "text", "text": "t_0123456789abcdef"}],
            },
        ],
    }
    assert DocV1.model_validate_json(out, strict=True) == DOC


def test_emit_is_deterministic() -> None:
    assert emit(DOC, vocab=REGISTRY) == emit(DOC, vocab=REGISTRY)


def test_type_check_refuses_subclass() -> None:
    doc = _SubDoc(profile=Profile.STRICT, source=SourceKind.HTML, vocab=(), body=())
    with pytest.raises(EgressError) as info:
        emit(doc, vocab=REGISTRY)
    _assert_clean(info.value, ErrorCode.E_EGRESS_TYPE)


def test_serialize_refuses_unknown_type() -> None:
    with pytest.raises(EgressError) as info:
        emit(_raw((object(),)), vocab=REGISTRY)
    _assert_clean(info.value, ErrorCode.E_EGRESS_SERIALIZE)


def test_schema_refuses_raw_string_leaf() -> None:
    doc = _raw((Text.model_construct(kind="text", text=cast(Token, "hello")),))
    with pytest.raises(EgressError) as info:
        emit(doc, vocab=REGISTRY)
    _assert_clean(info.value, ErrorCode.E_EGRESS_SCHEMA)


def test_jsonschema_refusal_maps_to_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "_VALIDATOR", _RejectAll())
    with pytest.raises(EgressError) as info:
        emit(DOC, vocab=REGISTRY)
    _assert_clean(info.value, ErrorCode.E_EGRESS_JSONSCHEMA)


@pytest.mark.parametrize(
    "doc",
    [
        pytest.param(_doc(vocab=(), body=(Element(tag=BTN),)), id="undeclared-vocab"),
        pytest.param(
            _doc(vocab=(VocabRef(id=STUB_VOCAB.id, version=2),), body=(Element(tag=BTN),)),
            id="version-mismatch",
        ),
        pytest.param(
            _doc(body=(Element(tag=EnumValue(vocab=STUB_VOCAB.id, term="nope")),)),
            id="term-not-in-vocabulary",
        ),
        pytest.param(
            _doc(vocab=(STUB_REF, STUB_REF)),
            id="duplicate-vocab-ids",
        ),
        pytest.param(_doc(vocab=(VocabRef(id="other.vocab", version=1),)), id="unknown-id"),
    ],
)
def test_vocab_refusal(doc: DocV1) -> None:
    with pytest.raises(EgressError) as info:
        emit(doc, vocab=REGISTRY)
    _assert_clean(info.value, ErrorCode.E_EGRESS_VOCAB)


def test_string_refusal_maps_to_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "_strings_ok", lambda node, declared: False)
    with pytest.raises(EgressError) as info:
        emit(DOC, vocab=REGISTRY)
    _assert_clean(info.value, ErrorCode.E_EGRESS_STRING)


def test_roundtrip_refusal_maps_to_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "_reserializes", lambda model, raw: False)
    with pytest.raises(EgressError) as info:
        emit(DOC, vocab=REGISTRY)
    _assert_clean(info.value, ErrorCode.E_EGRESS_ROUNDTRIP)


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        (
            {"schema_id": "asfb.doc/1", "body": [{"kind": "text", "text": "t_0123456789abcdef"}]},
            True,
        ),
        ({"body": [{"kind": "element", "tag": {"vocab": "test.stub", "term": "btn"}}]}, True),
        ({"body": [{"kind": "element", "attrs": [{"value": True}]}]}, True),
        ({"body": [None]}, True),
        ({"bogus": 1}, False),
        ({"body": [{"kind": "element", "bogus": 1}]}, False),
        ({"body": ["hello"]}, False),
        ({"body": [{"vocab": "test.stub", "term": "nope"}]}, False),
        ({"body": [1.5]}, False),
        ({"body": [{"kind": "text", "text": 1.5}]}, False),
    ],
)
def test_string_walker_direct(node: object, expected: bool) -> None:
    declared = {STUB_VOCAB.id: STUB_VOCAB}
    assert gate._strings_ok(node, declared) is expected  # noqa: SLF001


def test_reserializes_direct() -> None:
    raw = emit(DOC, vocab=REGISTRY)
    assert gate._reserializes(DOC, raw) is True  # noqa: SLF001
    assert gate._reserializes(DOC, raw + " ") is False  # noqa: SLF001


@pytest.mark.parametrize("raw", ['{"a": 1, "a": 2}', '{"a": NaN}', '{"a": Infinity}'])
def test_strict_parse_rejects_duplicates_and_non_finite(raw: str) -> None:
    with pytest.raises(ValueError, match=r"duplicate key|non-finite number"):
        gate._parse_strict(raw)  # noqa: SLF001


def test_token_with_trailing_newline_is_refused() -> None:
    # JSON Schema "$" accepts a trailing newline, so step 3b lets it through;
    # step 3 (pydantic) must reject it and the other steps must reject it too.
    doc = _raw((Text.model_construct(kind="text", text=cast(Token, "t_0123456789abcdef\n")),))
    with pytest.raises(EgressError) as info:
        emit(doc, vocab=REGISTRY)
    _assert_clean(info.value, ErrorCode.E_EGRESS_SCHEMA)


VALID_INSTANCE: dict[str, Any] = json.loads(emit(DOC, vocab=REGISTRY))


def test_jsonschema_validator_accepts_emitted_document() -> None:
    assert gate._VALIDATOR.is_valid(VALID_INSTANCE) is True  # noqa: SLF001


@pytest.mark.parametrize(
    "instance",
    [
        pytest.param({**VALID_INSTANCE, "body": ["hello"]}, id="raw-str-node"),
        pytest.param({**VALID_INSTANCE, "body": ["t_0123456789abcdef"]}, id="bare-token-node"),
        pytest.param({**VALID_INSTANCE, "bogus": 1}, id="extra-key"),
        pytest.param({**VALID_INSTANCE, "body": [1.5]}, id="float-node"),
        pytest.param(
            {**VALID_INSTANCE, "body": [{"kind": "script", "text": "t_0123456789abcdef"}]},
            id="unknown-kind",
        ),
        pytest.param(
            {**VALID_INSTANCE, "vocab": [{"id": "test.stub", "version": 65536}]},
            id="version-over-bound",
        ),
        pytest.param(
            {key: value for key, value in VALID_INSTANCE.items() if key != "body"},
            id="missing-body",
        ),
    ],
)
def test_jsonschema_validator_rejects(instance: dict[str, Any]) -> None:
    assert gate._VALIDATOR.is_valid(instance) is False  # noqa: SLF001


def test_allowlists_are_pinned() -> None:
    assert gate._KEYS == frozenset(  # noqa: SLF001
        {
            "id",
            "version",
            "vocab",
            "term",
            "schema_id",
            "profile",
            "source",
            "body",
            "kind",
            "tag",
            "attrs",
            "name",
            "value",
            "children",
            "text",
            "form",
            "html",
            "scheme",
            "authority",
            "rooted",
            "path",
            "trailing_slash",
            "query",
            "fragment",
        }
    )
    assert gate._WORDS == frozenset(  # noqa: SLF001
        {
            "asfb.doc/1",
            "strict",
            "diffable",
            "html",
            "element",
            "text",
            "opaque",
            "doctype",
            "comment",
            "cdata",
            "pi",
            "decl",
        }
    )


class _WrongId:
    id = "other.vocab"
    version = 1

    def __contains__(self, term: object, /) -> bool:
        return term in STUB_VOCAB.terms


def test_registry_entry_with_other_id_is_refused() -> None:
    doc = _doc(body=(Element(tag=BTN),))
    with pytest.raises(EgressError) as info:
        emit(doc, vocab={STUB_VOCAB.id: _WrongId()})
    _assert_clean(info.value, ErrorCode.E_EGRESS_VOCAB)
