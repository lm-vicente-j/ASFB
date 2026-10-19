import json
from typing import Any

import pytest

from asfb.egress import gate
from asfb.egress.gate import emit
from asfb.errors import EgressError, ErrorCode
from asfb.schema.doc import DocV1, Profile, SourceKind, VocabRef
from asfb.schema.html import Element, Node, Text
from asfb.types.token import Token
from asfb.types.values import EnumValue
from tests.stubs import STUB_VOCAB

TOKEN = Token("t_0123456789abcdef")
BTN = EnumValue(vocab=STUB_VOCAB.id, term="btn")
REF = VocabRef(id=STUB_VOCAB.id, version=1)
REGISTRY = {STUB_VOCAB.id: STUB_VOCAB}


def _chain(depth: int) -> Node:
    node: Node = Text(text=TOKEN)
    for _ in range(depth):
        node = Element(tag=BTN, children=(node,))
    return node


def _raw_chain(depth: int) -> Any:
    node: Any = Text.model_construct(kind="text", text=TOKEN)
    for _ in range(depth):
        node = Element.model_construct(kind="element", tag=BTN, attrs=(), children=(node,))
    return node


def _doc(node: Any, *, validated: bool) -> DocV1:
    if validated:
        return DocV1(profile=Profile.STRICT, source=SourceKind.HTML, vocab=(REF,), body=(node,))
    return DocV1.model_construct(
        profile=Profile.STRICT, source=SourceKind.HTML, vocab=(REF,), body=(node,)
    )


def _depth_of(instance: dict[str, Any]) -> int:
    depth = 0
    current: Any = instance["body"]
    while current:
        node = current[0]
        if node["kind"] != "element":
            break
        depth += 1
        current = node["children"]
    return depth


def test_depth_64_document_emits() -> None:
    doc = _doc(_chain(64), validated=True)
    out = emit(doc, vocab=REGISTRY)
    instance = json.loads(out)
    assert _depth_of(instance) == 64
    assert gate._VALIDATOR.is_valid(instance) is True  # noqa: SLF001
    assert DocV1.model_validate_json(out, strict=True) == doc


def test_depth_64_document_emit_is_deterministic() -> None:
    doc = _doc(_chain(64), validated=True)
    assert emit(doc, vocab=REGISTRY) == emit(doc, vocab=REGISTRY)


def test_depth_200_document_is_refused_cleanly() -> None:
    doc = _doc(_raw_chain(200), validated=False)
    with pytest.raises(EgressError) as info:
        emit(doc, vocab=REGISTRY)
    assert info.value.code is ErrorCode.E_EGRESS_SERIALIZE
    assert info.value.args == (ErrorCode.E_EGRESS_SERIALIZE.value,)
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


def test_declare_walks_deep_tree_without_recursion() -> None:
    node: Any = Text.model_construct(kind="text", text=TOKEN)
    for _ in range(3000):
        node = Element.model_construct(kind="element", tag=BTN, attrs=(), children=(node,))
    doc = _doc(node, validated=False)
    assert gate._declare(doc, REGISTRY) == {STUB_VOCAB.id: STUB_VOCAB}  # noqa: SLF001


def test_declare_refuses_undeclared_term_at_depth() -> None:
    bad = EnumValue(vocab=STUB_VOCAB.id, term="nope")
    node: Any = Element.model_construct(kind="element", tag=bad, attrs=(), children=())
    for _ in range(3000):
        node = Element.model_construct(kind="element", tag=BTN, attrs=(), children=(node,))
    doc = _doc(node, validated=False)
    assert gate._declare(doc, REGISTRY) is None  # noqa: SLF001


def test_strings_walker_handles_deep_nesting() -> None:
    nested: Any = []
    for _ in range(10000):
        nested = [nested]
    declared = {STUB_VOCAB.id: STUB_VOCAB}
    assert gate._strings_ok({"body": nested}, declared) is True  # noqa: SLF001


def test_profile_values_reach_egress() -> None:
    for profile in Profile:
        doc = DocV1(profile=profile, source=SourceKind.HTML, vocab=(), body=())
        assert json.loads(emit(doc, vocab=REGISTRY))["profile"] == profile.value
