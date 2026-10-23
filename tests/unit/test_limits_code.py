"""The Python source limits: 512 KiB, 20 000 lines, 50 000 lexemes. Each one is inclusive at its
bound, refuses the document as a whole with its static E_CODE_* code, and never truncates."""

import pathlib
import tokenize
from collections.abc import Callable, Iterator

import pytest

from asfb import api
from asfb.api import sanitize_python
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import code, limits
from asfb.schema.code import CodeV1
from asfb.session import Session


def _code_of(text: str) -> ErrorCode | None:
    try:
        code.read_text(text)
    except AsfbError as error:
        return error.code
    return None


def test_the_limits_have_their_documented_values() -> None:
    assert limits.MAX_CODE_BYTES == 524_288
    assert limits.MAX_CODE_LINES == 20_000
    assert limits.MAX_CODE_LEXEMES == 50_000


def test_bytes_are_inclusive_at_512_kib() -> None:
    line = "#" + "a" * 9998 + "\n"  # 10 000 bytes: each line within the line limit
    last = "#" + "a" * 4286 + "\n"  # 4 288 bytes: completes the 524 288 exactly
    text = line * 52 + last
    assert len(text.encode("utf-8")) == limits.MAX_CODE_BYTES
    assert _code_of(text) is None
    assert _code_of(text + "a") is ErrorCode.E_CODE_TOO_LARGE


def test_lines_are_inclusive_at_20_000() -> None:
    assert _code_of("\n" * 19_999 + "x\n") is None
    assert _code_of("\n" * 20_000 + "x\n") is ErrorCode.E_CODE_TOO_LARGE


def test_a_final_newline_does_not_add_a_line() -> None:
    assert _code_of("\n" * 19_999 + "x") is None  # 20 000 lines, the last one without newline


def test_lexemes_are_inclusive_at_50_000() -> None:
    line = "a;" * 3999 + "a\n"  # 7 999 tokens and one newline: 8 000 lexemes
    exact = line * 6 + "a;" * 999 + "a\n"  # 6 * 8 000 + 2 000 = 50 000 lexemes
    assert _code_of(exact) is None
    # One more lexeme, a newline after 2 000 tokens of the last line: 50 001 lexemes.
    assert _code_of(line * 6 + "a;" * 1000 + "\n") is ErrorCode.E_CODE_TOO_MANY_LEXEMES


def test_the_tokenizer_stops_at_the_limit_on_512_kib_of_one_character_lexemes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 512 KiB exactly, all of it one-character lexemes: 262 144 names and operators, far above the
    # limit. Ingest must stop the tokenizer at lexeme 50 001, not read the rest of the document.
    line = "a;" * 999 + "a\n"  # 2 000 bytes, 2 000 lexemes
    text = line * 262 + "a;" * 143 + "a\n"  # 262 * 2 000 + 288 = 524 288 bytes
    assert len(text.encode("utf-8")) == limits.MAX_CODE_BYTES
    real = tokenize.generate_tokens
    yielded = 0

    def counting(readline: Callable[[], str]) -> Iterator[tokenize.TokenInfo]:
        nonlocal yielded
        for token in real(readline):
            yielded += 1
            yield token

    monkeypatch.setattr(tokenize, "generate_tokens", counting)
    assert _code_of(text) is ErrorCode.E_CODE_TOO_MANY_LEXEMES
    assert yielded == limits.MAX_CODE_LEXEMES + 1


def test_an_fstring_is_one_lexeme(monkeypatch: pytest.MonkeyPatch) -> None:
    # x, =, the f-string and the newline: four lexemes whatever the f-string holds.
    text = 'x = f"{a}{b}{c}{d}"\n'
    model = sanitize_python(text).model
    assert isinstance(model, CodeV1)
    assert len(model.lexemes) == 4
    monkeypatch.setattr(limits, "MAX_CODE_LEXEMES", 4)
    assert _code_of(text) is None
    monkeypatch.setattr(limits, "MAX_CODE_LEXEMES", 3)
    assert _code_of(text) is ErrorCode.E_CODE_TOO_MANY_LEXEMES


def test_a_refused_document_gives_no_partial_output() -> None:
    with pytest.raises(AsfbError) as info:
        sanitize_python("\n" * 20_000 + "x\n")
    assert info.value.code is ErrorCode.E_CODE_TOO_LARGE


def test_a_python_file_over_its_limit_refuses_with_the_code_error(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "big.py"
    path.write_bytes(b"#" * (limits.MAX_CODE_BYTES + 1))
    with pytest.raises(AsfbError) as info:
        api.sanitize(path)
    assert info.value.code is ErrorCode.E_CODE_TOO_LARGE


def test_a_session_python_file_over_its_limit_refuses_with_the_code_error(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "big.py").write_bytes(b"#" * (limits.MAX_CODE_BYTES + 1))
    with pytest.raises(AsfbError) as info:
        Session([tmp_path]).sanitize("big.py")
    assert info.value.code is ErrorCode.E_CODE_TOO_LARGE


def test_bytes_are_checked_before_lines_and_lexemes() -> None:
    # Over 512 KiB on one line, also over the lexeme limit: the bytes come first.
    assert _code_of("a;" * 270_000 + "\n") is ErrorCode.E_CODE_TOO_LARGE


def test_lines_are_checked_before_lexemes() -> None:
    # 20 001 lines of four lexemes each: over the line limit and over the lexeme limit.
    assert _code_of("a;a\n" * 20_001) is ErrorCode.E_CODE_TOO_LARGE
