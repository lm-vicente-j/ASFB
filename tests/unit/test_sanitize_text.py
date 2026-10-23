"""Plain text: line splitting, censoring into paragraphs, and the (token, surface) pairs."""

import pytest

from asfb import normalize
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import split_lines
from asfb.ingest.limits import MAX_BYTES, MAX_LINES
from asfb.sanitize.text import censor_text_with_pairs
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import Profile
from asfb.schema.text import TextV1
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted, unwrap

KEY = SessionKey(bytes(range(32)))


def _censor(text: str) -> tuple[TextV1, tuple[tuple[str, str], ...]]:
    eol, lines = split_lines(text)
    model, pairs = censor_text_with_pairs(KEY, eol, lines, Profile.STRICT)
    return model, tuple((str(token), surface) for token, surface in pairs.items())


def _paragraph_texts(model: TextV1) -> list[list[tuple[int, str]]]:
    return [
        [(line.indent, line.text) for line in paragraph.lines] for paragraph in model.paragraphs
    ]


@pytest.mark.parametrize(
    ("text", "eol"),
    [
        ("a\nb\n", "lf"),
        ("a\r\nb\r\n", "crlf"),
        ("a\rb\r", "cr"),
        ("a\nb\r\n", "mixed"),
        ("a\nb", "lf"),
        ("single", "none"),
        ("", "none"),
    ],
)
def test_line_ending_kinds(text: str, eol: str) -> None:
    assert split_lines(text)[0] == eol


def test_trailing_line_ending_does_not_add_a_line() -> None:
    _eol, lines = split_lines("a\nb\n")
    assert [unwrap(line) for line in lines] == ["a", "b"]
    _eol, lines = split_lines("a\nb")
    assert [unwrap(line) for line in lines] == ["a", "b"]


def test_blank_lines_separate_paragraphs_and_repeats_count_once() -> None:
    model, _pairs = _censor("one\ntwo\n\n\n\nthree\n")
    assert [len(paragraph.lines) for paragraph in model.paragraphs] == [2, 1]


def test_lines_of_only_zero_width_characters_are_blank() -> None:
    model, _pairs = _censor("one\n​‍\nTwo\n")
    assert [len(paragraph.lines) for paragraph in model.paragraphs] == [1, 1]


def test_indentation_counts_spaces_and_tabs_and_is_capped() -> None:
    model, _pairs = _censor("  a\n\tb\n \t c\n" + " " * 100 + "d\n")
    indents = [indent for paragraph in _paragraph_texts(model) for indent, _token in paragraph]
    assert indents == [2, 4, 6, 64]


def test_indentation_stops_at_the_first_other_character() -> None:
    model, _pairs = _censor("a​  b\n")
    assert _paragraph_texts(model)[0][0][0] == 0


def test_same_line_gives_the_same_token() -> None:
    model, _pairs = _censor("Hello\n\nHello\n")
    first, second = _paragraph_texts(model)
    assert first[0][1] == second[0][1]


def test_nfc_and_nfd_lines_share_a_token_but_keep_distinct_surfaces() -> None:
    _model, pairs = _censor("café\n\ncafé\n")
    assert len({token for token, _surface in pairs}) == 1
    assert {surface for _token, surface in pairs} == {"café", "café"}


def test_surface_is_the_raw_line_with_outer_ascii_whitespace_stripped() -> None:
    _model, pairs = _censor("  \tpadded value \t\n")
    assert pairs == ((pairs[0][0], "padded value"),)


def test_every_pair_recomputes_its_token() -> None:
    _model, pairs = _censor("one\n  two​\n\nthree\n")
    for token, surface in pairs:
        assert token_for(KEY, Untrusted(surface)) == token


def test_every_token_of_the_model_has_a_pair() -> None:
    model, pairs = _censor("one\n\ntwo\nthree\n")
    minted = {token for token, _surface in pairs}
    for paragraph in model.paragraphs:
        for line in paragraph.lines:
            assert line.text in minted


def test_crlf_and_cr_give_the_same_tokens_as_lf() -> None:
    lf, _ = _censor("one\ntwo\n")
    crlf, _ = _censor("one\r\ntwo\r\n")
    cr, _ = _censor("one\rtwo\r")
    assert _paragraph_texts(lf) == _paragraph_texts(crlf) == _paragraph_texts(cr)


def test_a_bom_read_as_utf8_sig_is_not_a_token() -> None:
    text = b"\xef\xbb\xbfhello\n".decode("utf-8-sig")
    model, pairs = _censor(text)
    assert len(pairs) == 1
    assert pairs[0][1] == "hello"
    assert model.eol == "lf"


def test_line_limit_is_refused() -> None:
    with pytest.raises(AsfbError) as info:
        split_lines("a\n" * (MAX_LINES + 1))
    assert info.value.code is ErrorCode.E_TEXT_LINES


def test_line_limit_is_not_reached_at_the_limit() -> None:
    _eol, lines = split_lines("a\n" * MAX_LINES)
    assert len(lines) == MAX_LINES


def test_size_limit_is_refused_before_splitting() -> None:
    with pytest.raises(AsfbError) as info:
        split_lines("a" * (MAX_BYTES + 1))
    assert info.value.code is ErrorCode.E_SOURCE_SIZE


def test_a_hundred_thousand_character_line_is_one_token() -> None:
    model, pairs = _censor("x" * 100_000 + "\n")
    assert len(pairs) == 1
    assert len(model.paragraphs[0].lines) == 1


def test_non_string_input_is_refused() -> None:
    with pytest.raises(AsfbError) as info:
        split_lines(b"bytes")  # type: ignore[arg-type]
    assert info.value.code is ErrorCode.E_SOURCE_UNSUPPORTED


def test_normalize_drops_zero_width_before_the_blank_check() -> None:
    assert unwrap(normalize.value(Untrusted("​ ‍"))) == ""


def test_view_prints_indentation_and_tokens_with_one_blank_line_between_paragraphs() -> None:
    from asfb.egress import emit, render
    from asfb.vocab import registry

    model, _pairs = _censor("  one\n\ttwo\n\nthree\n")
    view = str(render(emit(model, vocab=registry())))
    lines = view.split("\n")
    assert lines[0] == "  " + _paragraph_texts(model)[0][0][1]
    assert lines[1] == "    " + _paragraph_texts(model)[0][1][1]
    assert lines[2] == ""
    assert lines[3] == _paragraph_texts(model)[1][0][1]
