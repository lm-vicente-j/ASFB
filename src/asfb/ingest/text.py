"""Plain text lines: split on CRLF, CR or LF, and report which line endings the source used.

The lines leave as Untrusted strings; nothing here is output. The limits are in limits.py. Public
functions raise AsfbError outside any except block, so no traceback carries input text.
"""

import re
from typing import Final, Literal

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest.limits import MAX_BYTES, MAX_LINES
from asfb.types.untrusted import Untrusted

Eol = Literal["lf", "crlf", "cr", "mixed", "none"]
_EOL_RE: Final = re.compile(r"\r\n|\r|\n")
_KINDS: Final[dict[str, Literal["lf", "crlf", "cr"]]] = {"\r\n": "crlf", "\r": "cr", "\n": "lf"}


def _eol(separators: list[str]) -> Eol:
    kinds = {_KINDS[separator] for separator in separators}
    if not kinds:
        return "none"
    if len(kinds) > 1:
        return "mixed"
    (kind,) = kinds
    return kind


def _split(text: str) -> tuple[Eol, tuple[str, ...]] | ErrorCode:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return ErrorCode.E_SOURCE_DECODE
    if size > MAX_BYTES:
        return ErrorCode.E_SOURCE_SIZE
    separators = _EOL_RE.findall(text)
    pieces = _EOL_RE.split(text)
    # A text that ends with a line ending does not have one more (empty) line.
    if len(pieces) > 1 and pieces[-1] == "":
        pieces.pop()
    if len(pieces) > MAX_LINES:
        return ErrorCode.E_TEXT_LINES
    return _eol(separators), tuple(pieces)


def _checked(text: str) -> tuple[Eol, tuple[str, ...]]:
    if type(text) is not str:
        raise AsfbError(ErrorCode.E_SOURCE_UNSUPPORTED)
    outcome = _split(text)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def split_lines(text: str, /) -> tuple[Eol, tuple[Untrusted[str], ...]]:
    """Split a text into its lines and report its line ending kind. Raises AsfbError."""
    eol, pieces = _checked(text)
    return eol, tuple(Untrusted(piece) for piece in pieces)


def plain_lines(text: str, /) -> tuple[str, ...]:
    """The same lines as split_lines, as plain strings, for the markdown scanner.

    The scanner matches its rules on these strings and wraps every block text in Untrusted
    before it leaves ingest. Raises AsfbError with the same codes as split_lines.
    """
    _eol, pieces = _checked(text)
    return pieces
