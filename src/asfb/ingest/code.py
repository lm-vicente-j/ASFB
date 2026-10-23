"""Python source: check the encoding, normalize, and lex with the stdlib tokenizer.

The lexer runs in the isolated worker (through sandbox/worker.py). It returns only kinds and
offsets into the normalized text: no token text leaves ingest. The text itself is wrapped in
Untrusted, and only sanitize/code.py unwraps it to build surfaces. Only the tokenizers of the
supported runtimes are used; any other runtime refuses with E_CODE_TOKENIZE_FAILED.

No ast, compile or exec: the stdlib tokenizer is the only parser here. Public functions raise
AsfbError outside any except block, so no traceback carries source text.
"""

import io
import re
import sys
import tokenize
from dataclasses import dataclass
from typing import Final

from asfb import normalize
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import limits
from asfb.types.untrusted import Untrusted

_SUPPORTED: Final = frozenset({(3, 12), (3, 13), (3, 14)})
_SKIP: Final = frozenset({"NL", "ENDMARKER", "ENCODING"})
_KEEP: Final = frozenset(
    {
        "NAME",
        "NUMBER",
        "STRING",
        "OP",
        "COMMENT",
        "NEWLINE",
        "INDENT",
        "DEDENT",
        "FSTRING_START",
        "FSTRING_MIDDLE",
        "FSTRING_END",
        "TSTRING_START",
        "TSTRING_MIDDLE",
        "TSTRING_END",
    }
)
_INDENT_MESSAGE: Final = "too many levels of indentation"
_NEWLINE: Final = re.compile("\n")
# PEP 263: a coding cookie on the first line, or on the second when the first is blank or a comment.
_COOKIE: Final = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)", re.ASCII)
_SKIPPABLE: Final = re.compile(rb"^[ \t\f]*(?:#.*)?$", re.ASCII)
_UTF8_NAMES: Final = frozenset({"utf-8", "utf8"})
_FSTRING_OPEN: Final = frozenset({"FSTRING_START", "TSTRING_START"})
_FSTRING_CLOSE: Final = frozenset({"FSTRING_END", "TSTRING_END"})


@dataclass(frozen=True, slots=True)
class RawToken:
    """One significant token: its tokenize kind and its span in the normalized text."""

    kind: str
    start: int
    end: int


@dataclass(frozen=True, slots=True, eq=False)
class CodeSource:
    """A normalized Python source: the text (wrapped), the dropped count and the tokens."""

    text: Untrusted[str]
    dropped: int
    tokens: tuple[RawToken, ...]


def _cookie_error(data: bytes) -> ErrorCode | None:
    """An encoding cookie in the first two lines must name UTF-8, or the source is refused.

    A plain regex on the bytes, not tokenize.detect_encoding: the codec lookup it does imports
    modules, and the code job refuses to compile source, so an import there would fail.
    """
    for line in data.split(b"\n", 2)[:2]:
        found = _COOKIE.match(line)
        if found is not None:
            name = found.group(1).decode("ascii").lower().replace("_", "-")
            if name in _UTF8_NAMES or name.startswith("utf-8-"):
                return None
            return ErrorCode.E_CODE_ENCODING_UNSUPPORTED
        if _SKIPPABLE.match(line) is None:
            break
    return None


def decoded(data: bytes, /) -> str | ErrorCode:
    """UTF-8 text of a Python source file (a BOM is dropped), or the static code. Never raises."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ErrorCode.E_CODE_ENCODING_UNSUPPORTED


def decode(data: bytes, /) -> str:
    """UTF-8 text of a Python source file (a BOM is dropped). Raises AsfbError with a code."""
    outcome = decoded(data)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def _starts(text: str) -> list[int]:
    """The offset where each line starts; line n (1-based) starts at index n - 1."""
    starts = [0]
    starts.extend(match.end() for match in _NEWLINE.finditer(text))
    return starts


def _offset(starts: list[int], size: int, row: int, col: int) -> int:
    if row > len(starts):
        return size
    return min(starts[row - 1] + col, size)


def _line_count(text: str) -> int:
    """Lines of a text: a final newline ends the last line, it does not start another."""
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _lines_ok(text: str) -> ErrorCode | None:
    if _line_count(text) > limits.MAX_CODE_LINES:
        return ErrorCode.E_CODE_TOO_LARGE
    for line in text.split("\n"):
        if len(line) > limits.MAX_CODE_LINE:
            return ErrorCode.E_CODE_LINE_TOO_LONG
    return None


def _lex(text: str) -> tuple[RawToken, ...] | ErrorCode:
    if sys.version_info[:2] not in _SUPPORTED:
        return ErrorCode.E_CODE_TOKENIZE_FAILED
    starts = _starts(text)
    size = len(text)
    tokens: list[RawToken] = []
    depth = 0
    lexemes = 0  # what censor_code emits: an f-string, from its start to its end, is one lexeme
    fstring = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            name = tokenize.tok_name.get(token.type, "")
            if name in _SKIP:
                continue
            if name not in _KEEP:
                return ErrorCode.E_CODE_TOKENIZE_FAILED
            if name == "INDENT":
                depth += 1
                if depth > limits.MAX_CODE_INDENT:
                    return ErrorCode.E_CODE_INDENT_TOO_DEEP
            elif name == "DEDENT":
                depth -= 1
            if name in _FSTRING_OPEN:
                if fstring == 0:
                    lexemes += 1
                fstring += 1
            elif name in _FSTRING_CLOSE:
                fstring -= 1
            elif fstring == 0:
                lexemes += 1
            if lexemes > limits.MAX_CODE_LEXEMES:
                return ErrorCode.E_CODE_TOO_MANY_LEXEMES
            start = _offset(starts, size, *token.start)
            end = _offset(starts, size, *token.end)
            if len(text[start:end].encode("utf-8")) > limits.MAX_CODE_TOKEN_BYTES:
                return ErrorCode.E_CODE_TOO_LARGE
            tokens.append(RawToken(name, start, end))
    except (tokenize.TokenError, SyntaxError, ValueError) as error:
        if _INDENT_MESSAGE in str(error):
            return ErrorCode.E_CODE_INDENT_TOO_DEEP
        return ErrorCode.E_CODE_TOKENIZE_FAILED
    return tuple(tokens)


def _read(text: str) -> CodeSource | ErrorCode:
    if type(text) is not str:
        return ErrorCode.E_SOURCE_UNSUPPORTED
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        return ErrorCode.E_CODE_ENCODING_UNSUPPORTED
    if len(data) > limits.MAX_CODE_BYTES:
        return ErrorCode.E_CODE_TOO_LARGE
    cookie = _cookie_error(data)
    if cookie is not None:
        return cookie
    normalized, dropped = normalize.code_text(text)
    lines = _lines_ok(normalized)
    if lines is not None:
        return lines
    tokens = _lex(normalized)
    if isinstance(tokens, ErrorCode):
        return tokens
    return CodeSource(Untrusted(normalized), dropped, tokens)


def read_text(text: str, /) -> CodeSource:
    """Normalize and lex a Python source given as text. Raises AsfbError with a static code."""
    outcome = _read(text)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome
