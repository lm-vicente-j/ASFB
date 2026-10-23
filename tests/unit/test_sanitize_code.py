"""Python censoring: public lexemes, tokens, f-strings, file names, pairs and spans."""

import json

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import code
from asfb.sanitize.code import CodeCensus, censor_code
from asfb.sanitize.tokens import token_for
from asfb.schema import code as schema
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted, unwrap
from asfb.types.values import EnumValue

KEY = SessionKey(bytes(range(32)))


def _census(text: str, name: str | None = None) -> CodeCensus:
    return censor_code(KEY, code.read_text(text), Profile.STRICT, name)


def _kinds(census: CodeCensus) -> list[str]:
    return [lexeme.kind for lexeme in census.model.lexemes]


def _dump(census: CodeCensus) -> str:
    return json.dumps(census.model.model_dump(mode="json"))


def test_keywords_builtins_and_stdlib_names_are_public() -> None:
    census = _census("def f(x):\n    return len(os.path.join(x))\n")
    kinds = _kinds(census)
    assert kinds[:4] == ["keyword", "name", "op", "name"]
    assert "builtin" in kinds
    assert "stdlib" in kinds
    values = [lexeme.value for lexeme in census.model.lexemes if lexeme.kind == "keyword"]
    assert values == [
        EnumValue(vocab="py.keywords", term="def"),
        EnumValue(vocab="py.keywords", term="return"),
    ]


def test_user_names_are_tokens_and_their_surfaces_are_minted() -> None:
    census = _census("secret_amount = 1\n")
    name = census.model.lexemes[0]
    assert name.kind == "name"
    assert isinstance(name.value, Token)
    surfaces = {surface for token, surface in census.pairs.items() if token == name.value}
    assert surfaces == {"secret_amount"}
    assert "secret_amount" not in _dump(census)


def test_zero_and_one_are_public_and_other_numbers_are_tokens() -> None:
    census = _census("a = 0\nb = 1\nc = 2\n")
    numbers = [lexeme.value for lexeme in census.model.lexemes if lexeme.kind == "number"]
    assert numbers[:2] == ["0", "1"]
    assert isinstance(numbers[2], Token)
    assert '"value": "2"' not in _dump(census)


def test_operators_are_closed_literals_and_newline_has_no_value() -> None:
    census = _census("x = (1 + 2)\n")
    ops = [lexeme.value for lexeme in census.model.lexemes if lexeme.kind == "op"]
    assert ops == ["=", "(", "+", ")"]
    newline = census.model.lexemes[-1]
    assert newline.kind == "newline"
    assert newline.value is None


def test_indent_and_dedent_carry_the_depth() -> None:
    census = _census("if x:\n    if y:\n        z\n")
    depths = [
        (lexeme.kind, lexeme.depth) for lexeme in census.model.lexemes if lexeme.depth is not None
    ]
    assert depths == [("indent", 1), ("indent", 2), ("dedent", 1), ("dedent", 0)]


def test_strings_keep_their_prefix_class_and_whole_text_is_one_token() -> None:
    census = _census("a = 'x'\nb = rb'y'\nc = f'{z}'\nd = u'w'\n")
    prefixes = [lexeme.prefix for lexeme in census.model.lexemes if lexeme.kind == "string"]
    assert prefixes == ["str", "bytes", "fstring", "str"]
    assert "'x'" not in _dump(census)


def test_docstrings_are_tokens() -> None:
    census = _census('def f():\n    """secret doc"""\n')
    assert "secret doc" not in _dump(census)
    assert [lexeme.kind for lexeme in census.model.lexemes if lexeme.kind == "string"] == ["string"]


def test_comment_body_is_a_token_and_the_hash_is_not_in_the_token() -> None:
    census = _census("x = 1  # TODO fix\n")
    comment = [lexeme for lexeme in census.model.lexemes if lexeme.kind == "comment"]
    assert len(comment) == 1
    assert "TODO" not in _dump(census)
    surface = [surface for token, surface in census.pairs.items() if token == comment[0].value]
    assert surface == ["TODO fix"]


def test_fstring_interpolated_variable_name_never_appears() -> None:
    census = _census("msg = f'hello {secret_user_name} and {other!r:>10}'\n")
    dumped = _dump(census)
    assert "secret_user_name" not in dumped
    assert "other" not in dumped
    strings = [lexeme for lexeme in census.model.lexemes if lexeme.kind == "string"]
    assert len(strings) == 1
    assert strings[0].prefix == "fstring"


def test_nested_fstrings_are_one_lexeme_each_level_of_nesting_is_hidden() -> None:
    census = _census("v = f\"{f'{hidden_name}'}\"\n")
    assert "hidden_name" not in _dump(census)
    assert _kinds(census).count("string") == 1


def test_fstring_span_covers_the_whole_literal_text() -> None:
    text = "x = f'{a}-{b}'\n"
    census = _census(text)
    source = unwrap(code.read_text(text).text)
    spans = [span for lexeme, span in zip(census.model.lexemes, census.spans, strict=True)]
    fstring = [
        span
        for lexeme, span in zip(census.model.lexemes, spans, strict=True)
        if lexeme.kind == "string"
    ]
    assert source[fstring[0][0] : fstring[0][1]] == "f'{a}-{b}'"


def test_spans_slice_the_source_to_each_lexeme_text() -> None:
    text = "def g(é):\n    return 'ü'\n"
    census = _census(text)
    source = unwrap(code.read_text(text).text)
    assert len(census.spans) == len(census.model.lexemes)
    assert source[census.spans[1][0] : census.spans[1][1]] == "g"


def test_lines_are_the_start_line_of_each_lexeme() -> None:
    census = _census("a = 1\n\nb = 2\n")
    assert [lexeme.line for lexeme in census.model.lexemes if lexeme.kind == "name"] == [1, 3]


def test_homoglyphs_are_not_public_terms() -> None:
    census = _census("d\u0435f = 1\n")
    assert _kinds(census)[0] == "name"


def test_bidi_controls_are_dropped_and_counted_not_emitted() -> None:
    census = _census("if access:  # \u202e\u2066fake\n    pass\n")
    assert census.model.dropped == 2
    assert "\u202e" not in _dump(census)


def test_file_name_is_a_token_and_the_extension_is_public() -> None:
    census = _census("x = 1\n", "payroll.py")
    assert census.model.ext == "py"
    assert isinstance(census.model.file, Token)
    assert census.model.file == token_for(KEY, Untrusted("payroll"))
    assert "payroll" not in _dump(census)


def test_stub_extension_is_public_and_others_are_refused() -> None:
    assert _census("x = 1\n", "a.pyi").model.ext == "pyi"
    with pytest.raises(AsfbError) as info:
        _census("x = 1\n", "a.txt")
    assert info.value.code is ErrorCode.E_SOURCE_UNSUPPORTED


def test_file_name_with_separators_is_refused() -> None:
    with pytest.raises(AsfbError) as info:
        _census("x = 1\n", "dir/a.py")
    assert info.value.code is ErrorCode.E_SOURCE_UNSUPPORTED


def test_without_a_name_the_file_fields_are_none() -> None:
    census = _census("x = 1\n")
    assert census.model.file is None
    assert census.model.ext is None


def test_every_token_has_a_pair_that_its_key_mints() -> None:
    census = _census("def f(a, b='s'):\n    return a  # c\n")
    tokens = {lexeme.value for lexeme in census.model.lexemes if isinstance(lexeme.value, Token)}
    paired = {token for token, _surface in census.pairs.items()}
    assert tokens <= paired
    for token, surface in census.pairs.items():
        assert token_for(KEY, Untrusted(surface)) == token


def test_vocabulary_refs_are_the_four_python_vocabularies() -> None:
    census = _census("x = 1\n")
    assert [ref.id for ref in census.model.vocab] == list(schema.PY_VOCAB_IDS)


def test_a_string_with_a_non_public_prefix_class_is_refused_by_the_model() -> None:
    with pytest.raises(ValueError, match="string kind needs"):
        schema.Lexeme(line=1, kind="string", value=None, prefix="str", depth=None)


def test_missing_vocabulary_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from asfb.sanitize import code as sanitize_code

    monkeypatch.setattr(sanitize_code, "py_registry", lambda: {})
    with pytest.raises(AsfbError) as info:
        _census("x = 1\n")
    assert info.value.code is ErrorCode.E_VOCAB_DATA
