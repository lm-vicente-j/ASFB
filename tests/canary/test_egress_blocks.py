from typing import Any

import pytest
from pydantic import SerializerFunctionWrapHandler, model_serializer

from asfb.egress.gate import emit
from asfb.errors import EgressError, ErrorCode
from asfb.sanitize.tokens import tokenize
from asfb.schema.doc import DocV1, Profile, VocabRef
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted
from asfb.types.values import EnumValue
from tests.canary.harness import find_leaks, new_canary
from tests.stubs import STUB_VOCAB

REGISTRY = {STUB_VOCAB.id: STUB_VOCAB}
STUB_REF = VocabRef(id=STUB_VOCAB.id, version=1)


def _texts(error: EgressError) -> list[str]:
    return [
        str(error),
        repr(error),
        repr(error.args),
        *(str(arg) for arg in error.args),
        repr(error.__cause__),
        repr(error.__context__),
    ]


def _assert_blocked(
    doc: DocV1,
    code: ErrorCode,
    forms: list[str],
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(EgressError) as info:
        emit(doc, vocab=REGISTRY)
    error = info.value
    assert error.code is code
    assert error.args == (code.value,)
    assert error.__cause__ is None
    assert error.__context__ is None
    captured = capfd.readouterr()
    assert find_leaks([*_texts(error), caplog.text, captured.out, captured.err], forms) == []


def test_raw_str_leaf_via_model_construct_is_refused(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    raw_items: tuple[Any, ...] = (canary,)
    doc = DocV1.model_construct(profile=Profile.STRICT, vocab=(STUB_REF,), items=raw_items)
    _assert_blocked(doc, ErrorCode.E_EGRESS_SCHEMA, [canary], caplog, capfd)


def test_enum_term_canary_is_refused(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    term = EnumValue(vocab=STUB_VOCAB.id, term=canary)
    doc = DocV1(profile=Profile.STRICT, vocab=(STUB_REF,), items=(term,))
    _assert_blocked(doc, ErrorCode.E_EGRESS_VOCAB, [canary], caplog, capfd)


def test_canary_inside_vocab_id_is_refused(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    vocab_id = canary.lower().replace("_", ".")
    doc = DocV1(
        profile=Profile.STRICT,
        vocab=(VocabRef(id=vocab_id, version=1),),
        items=(EnumValue(vocab=vocab_id, term="btn"),),
    )
    _assert_blocked(doc, ErrorCode.E_EGRESS_VOCAB, [canary, vocab_id], caplog, capfd)


def test_vocab_version_mismatch_is_refused(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    token = tokenize(SessionKey.generate(), Untrusted(canary))
    doc = DocV1(
        profile=Profile.STRICT,
        vocab=(VocabRef(id=STUB_VOCAB.id, version=2),),
        items=(token, EnumValue(vocab=STUB_VOCAB.id, term="btn")),
    )
    _assert_blocked(doc, ErrorCode.E_EGRESS_VOCAB, [canary], caplog, capfd)


def test_subclass_with_leaking_serializer_is_refused(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()

    class _Leaky(DocV1):
        @model_serializer(mode="wrap")
        def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
            return {"schema_id": "asfb.doc/1", "profile": "strict", "vocab": [], "items": [canary]}

    doc = _Leaky(profile=Profile.STRICT, vocab=(), items=())
    _assert_blocked(doc, ErrorCode.E_EGRESS_TYPE, [canary], caplog, capfd)
