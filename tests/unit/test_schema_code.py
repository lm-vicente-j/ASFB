"""The v1 Python model: its validators, its committed JSON Schema and its egress."""

import json
from importlib import resources

import pytest
from pydantic import ValidationError

from asfb.egress import emit, render
from asfb.errors import EgressError
from asfb.ingest import code
from asfb.sanitize.code import censor_code
from asfb.schema import CODE_RESOURCE
from asfb.schema.code import CodeV1, Lexeme
from asfb.schema.doc import Profile, VocabRef
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.values import EnumValue
from asfb.vocab import registry

TOKEN = Token("t_0123456789abcdef")
KEY = SessionKey(bytes(range(32)))


def _lexeme(**fields: object) -> Lexeme:
    base: dict[str, object] = {"line": 1, "kind": "name", "value": TOKEN, "prefix": None}
    base["depth"] = None
    base.update(fields)
    return Lexeme.model_validate(base)


def _refused(**fields: object) -> None:
    with pytest.raises(ValidationError):
        _lexeme(**fields)


def test_committed_schema_is_the_model_schema() -> None:
    committed = resources.files("asfb.schema").joinpath(CODE_RESOURCE).read_text(encoding="utf-8")
    assert json.loads(committed) == CodeV1.model_json_schema()


def test_committed_schema_file_is_stable_text() -> None:
    committed = resources.files("asfb.schema").joinpath(CODE_RESOURCE).read_text(encoding="utf-8")
    assert committed == json.dumps(CodeV1.model_json_schema(), indent=2, sort_keys=True) + "\n"


def test_term_kinds_need_their_own_vocabulary() -> None:
    _lexeme(kind="keyword", value=EnumValue(vocab="py.keywords", term="def"))
    _refused(kind="keyword", value=EnumValue(vocab="py.builtins", term="def"))
    _refused(kind="stdlib", value=TOKEN)
    _refused(kind="exception", value=EnumValue(vocab="py.keywords", term="def"))


def test_term_kinds_take_no_prefix_or_depth() -> None:
    _refused(kind="keyword", value=EnumValue(vocab="py.keywords", term="def"), prefix="str")
    _refused(kind="keyword", value=EnumValue(vocab="py.keywords", term="def"), depth=1)


def test_op_must_be_one_of_the_closed_operators() -> None:
    _lexeme(kind="op", value="(")
    _refused(kind="op", value="?")
    _refused(kind="op", value=TOKEN)


def test_numbers_are_zero_one_or_a_token() -> None:
    _lexeme(kind="number", value="0")
    _lexeme(kind="number", value="1")
    _lexeme(kind="number", value=TOKEN)
    _refused(kind="number", value="2")
    _refused(kind="number", value=None)


def test_name_and_comment_need_a_token() -> None:
    _lexeme(kind="comment", value=TOKEN)
    _refused(kind="name", value="(")
    _refused(kind="comment", value=None)


def test_string_needs_a_token_and_a_prefix_class() -> None:
    _lexeme(kind="string", value=TOKEN, prefix="fstring")
    _refused(kind="string", value=TOKEN, prefix=None)
    _refused(kind="string", value=TOKEN, prefix="unicode")


def test_newline_takes_nothing() -> None:
    _lexeme(kind="newline", value=None)
    _refused(kind="newline", value=None, depth=0)


def test_indent_and_dedent_need_a_depth_only() -> None:
    _lexeme(kind="indent", value=None, depth=1)
    _lexeme(kind="dedent", value=None, depth=0)
    _refused(kind="indent", value=None, depth=None)
    _refused(kind="dedent", value=TOKEN, depth=0)


def test_line_must_be_positive() -> None:
    _refused(line=0)


def _model(**fields: object) -> CodeV1:
    base: dict[str, object] = {
        "profile": Profile.STRICT,
        "vocab": tuple(
            VocabRef(id=vocab_id, version=1)
            for vocab_id in ("py.builtins", "py.exceptions", "py.keywords", "py.stdlib")
        ),
        "file": None,
        "ext": None,
        "dropped": 0,
        "lexemes": (),
    }
    base.update(fields)
    return CodeV1.model_validate(base)


def test_file_and_extension_come_together() -> None:
    _model(file=TOKEN, ext="py")
    with pytest.raises(ValidationError):
        _model(file=TOKEN, ext=None)
    with pytest.raises(ValidationError):
        _model(file=None, ext="py")


def test_the_model_declares_exactly_the_four_python_vocabularies() -> None:
    with pytest.raises(ValidationError):
        _model(vocab=(VocabRef(id="py.keywords", version=1),))


def test_emitted_json_round_trips_and_the_view_is_canonical() -> None:
    source = "def f(a):\n    # note\n    return a + 0\n"
    model = censor_code(KEY, code.read_text(source), Profile.STRICT, "tool.py").model
    emitted = emit(model, vocab=registry())
    view = str(render(emitted))
    assert view.splitlines()[0].endswith(".py")
    assert "def t_" in view
    assert "    # t_" in view
    assert "\n    return t_" in view
    assert view.endswith(" + 0")
    assert "note" not in view
    assert "tool" not in view


def test_a_tampered_document_is_refused_by_egress() -> None:
    model = _model(dropped=0, lexemes=())
    tampered = model.model_copy(update={"language": "ruby"})
    with pytest.raises(EgressError):
        emit(tampered, vocab=registry())


def test_egress_refuses_an_unknown_vocabulary_term() -> None:
    model = _model(
        lexemes=(
            Lexeme(
                line=1,
                kind="keyword",
                value=EnumValue(vocab="py.keywords", term="notakeyword"),
                prefix=None,
                depth=None,
            ),
        )
    )
    with pytest.raises(EgressError):
        emit(model, vocab=registry())
