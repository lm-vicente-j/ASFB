"""The first exit of asfb.code/1 is structural; render() runs the full gate on the same text."""

from typing import Any, cast

import pytest

from asfb import api
from asfb.egress import emit, emit_before_render, render
from asfb.errors import EgressError, ErrorCode
from asfb.schema.code import CodeV1, Lexeme
from asfb.types.values import EnumValue
from asfb.vocab import registry


def _model() -> CodeV1:
    model = api.sanitize_python("def f(x):\n    return x  # note\n").model
    assert isinstance(model, CodeV1)
    return model


def test_first_exit_gives_the_same_text_as_the_full_gate() -> None:
    model = _model()
    assert emit_before_render(model, vocab=registry()) == emit(model, vocab=registry())


def test_structural_refusal_is_typed() -> None:
    model = CodeV1.model_construct(
        schema_id="asfb.code/1",
        profile=_model().profile,
        language="python",
        vocab=(),
        file=None,
        ext=None,
        dropped=0,
        lexemes=cast(Any, ("not a lexeme",)),
    )
    with pytest.raises(EgressError) as info:
        emit_before_render(model, vocab=registry())
    assert info.value.code is ErrorCode.E_EGRESS_SERIALIZE


def test_render_refuses_what_the_structural_exit_lets_through() -> None:
    # The term is not in py.keywords: the typed model accepts it, the vocabulary walk does not.
    base = _model()
    bad = Lexeme.model_construct(
        line=1,
        kind="keyword",
        value=EnumValue(vocab="py.keywords", term="notakeyword"),
        prefix=None,
        depth=None,
    )
    model = base.model_copy(update={"lexemes": (bad,)})
    text = emit_before_render(model, vocab=registry())
    with pytest.raises(EgressError) as info:
        render(text)
    assert info.value.code is ErrorCode.E_VIEW
