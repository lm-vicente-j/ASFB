"""Python ingest: the encoding cookie, the limits, the normalizer and the stdlib lexer."""

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import code, limits
from asfb.normalize import code_text
from asfb.types.untrusted import unwrap


def _refused(text: str) -> ErrorCode:
    with pytest.raises(AsfbError) as info:
        code.read_text(text)
    return info.value.code


def _spans(text: str) -> list[tuple[str, str]]:
    source = code.read_text(text)
    body = unwrap(source.text)
    return [(token.kind, body[token.start : token.end]) for token in source.tokens]


def test_latin1_cookie_is_refused_with_its_code() -> None:
    assert _refused("# -*- coding: latin-1 -*-\nx = 1\n") is ErrorCode.E_CODE_ENCODING_UNSUPPORTED


def test_utf8_cookie_is_accepted() -> None:
    assert _spans("# -*- coding: utf-8 -*-\nx = 1\n")[0] == ("COMMENT", "# -*- coding: utf-8 -*-")


def test_latin1_bytes_fail_decode_with_the_encoding_code() -> None:
    data = "x = 'é'\n".encode("latin-1")
    with pytest.raises(AsfbError) as info:
        code.decode(data)
    assert info.value.code is ErrorCode.E_CODE_ENCODING_UNSUPPORTED


def test_decode_drops_a_utf8_bom() -> None:
    assert code.decode("\ufeffx = 1\n".encode("utf-8")) == "x = 1\n"


def test_surfaces_are_character_offsets_for_non_ascii_text() -> None:
    assert _spans("é = 'ü'\n")[:3] == [("NAME", "é"), ("OP", "="), ("STRING", "'ü'")]


def test_format_characters_are_dropped_and_counted() -> None:
    source = code.read_text("x\u200b = \u202e1\n")
    assert source.dropped == 2
    assert _spans("x\u200b = \u202e1\n")[0] == ("NAME", "x")
    assert unwrap(source.text) == "x = 1\n"


def test_normalizer_keeps_tab_lf_cr_and_form_feed() -> None:
    text, dropped = code_text("a\tb\x0cc\r\nd\re\n\x00\x07")
    assert text == "a\tb\x0cc\nd\ne\n"
    assert dropped == 2


def test_crlf_and_lone_cr_fold_to_lf() -> None:
    assert unwrap(code.read_text("a\r\nb\rc\n").text) == "a\nb\nc\n"


def test_fstring_pieces_are_tokenized_for_the_sanitizer() -> None:
    kinds = [kind for kind, _text in _spans('f"a{x}b"\n')]
    assert kinds[:4] == ["FSTRING_START", "FSTRING_MIDDLE", "OP", "NAME"]
    assert kinds[-2:] == ["FSTRING_END", "NEWLINE"]


def test_unterminated_triple_string_fails_tokenize() -> None:
    assert _refused("s = '''open\n") is ErrorCode.E_CODE_TOKENIZE_FAILED


def test_unknown_runtime_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code, "_SUPPORTED", frozenset({(2, 7)}))
    assert _refused("x = 1\n") is ErrorCode.E_CODE_TOKENIZE_FAILED


def test_size_limit_counts_utf8_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _refused("#" + "é" * (limits.MAX_CODE_BYTES // 2 + 1)) is ErrorCode.E_CODE_TOO_LARGE


def test_line_limit_is_inclusive() -> None:
    accepted = "a" * limits.MAX_CODE_LINE
    assert _spans(accepted)[0] == ("NAME", accepted)
    assert _refused("a" * (limits.MAX_CODE_LINE + 1)) is ErrorCode.E_CODE_LINE_TOO_LONG


def test_too_many_lines_is_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_CODE_LINES", 3)
    assert _refused("\n\n\n\n") is ErrorCode.E_CODE_TOO_LARGE


def test_too_many_lexemes_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_CODE_LEXEMES", 3)
    assert _refused("x = 1\n") is ErrorCode.E_CODE_TOO_MANY_LEXEMES


def test_one_lexeme_over_the_byte_limit_is_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_CODE_TOKEN_BYTES", 4)
    assert _refused('"""abcdefgh"""\n') is ErrorCode.E_CODE_TOO_LARGE


def test_deep_indentation_is_refused_with_its_code() -> None:
    levels = limits.MAX_CODE_INDENT + 2
    text = "".join("  " * depth + "if x:\n" for depth in range(levels)) + "  " * levels + "pass\n"
    assert _refused(text) is ErrorCode.E_CODE_INDENT_TOO_DEEP


def test_moderate_indentation_is_accepted() -> None:
    text = "".join("  " * depth + "if x:\n" for depth in range(20)) + "  " * 20 + "pass\n"
    assert any(kind == "INDENT" for kind, _text in _spans(text))


def test_non_string_input_is_unsupported() -> None:
    with pytest.raises(AsfbError) as info:
        code.read_text(b"x = 1")  # type: ignore[arg-type]
    assert info.value.code is ErrorCode.E_SOURCE_UNSUPPORTED
