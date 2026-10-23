"""Pure rules of edit: what text the agent may send, how newlines and tokens are matched.

No I/O, and no import of egress, sanitize or vault. The session reads and writes the file,
censors the result and records the pairs; everything here works on strings the caller holds.
Inner checks return codes; the caller raises AsfbError outside any except block.

The agent's text is literal ASCII (printable, TAB, LF, CR) plus tokens. A literal word must be a
term of a declared public vocabulary, so the text cannot carry original content. Surfaces are
inserted as they were decoded from the file, never re-escaped.
"""

import itertools
import re
from collections.abc import Container, Mapping
from typing import Final

from asfb.errors import ErrorCode

TOKEN_RE: Final = re.compile(r"t_[0-9a-f]{16}")
# In Python text a token is a whole word: t_ inside a longer identifier is not one.
CODE_TOKEN_RE: Final = re.compile(r"(?<![A-Za-z0-9_])t_[0-9a-f]{16}(?![A-Za-z0-9_])")
IDENTIFIER_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MAX_NEW_NAME: Final = 32
MAX_EDIT_BYTES: Final = 65_536
MAX_COMBINATIONS: Final = 16
HTML_UNSAFE: Final = frozenset("&<>\"'")
_NUL: Final = "\x00"
_WORD_RE: Final = re.compile(r"[A-Za-z0-9_.:/+-]+")
_PIECE_RE: Final = re.compile(r"[.:/+]")
_ALLOWED_CONTROLS: Final = frozenset("\t\n\r")


def _known(word: str, terms: Container[str]) -> bool:
    return word in terms or word.lower() in terms


def _word_ok(word: str, terms: Container[str]) -> bool:
    if _known(word, terms):
        return True
    return all(_known(piece, terms) for piece in _PIECE_RE.split(word) if piece)


def check_text(text: object, terms: Container[str], /) -> ErrorCode | None:
    """The first failing rule for one piece of agent text, or None when it may be used.

    Size first, then characters, then words: every literal word must be a term (or a piece of a
    dotted word is a term) of the document's declared vocabularies. Tokens are not literals.
    """
    if type(text) is not str:
        return ErrorCode.E_EDIT_INVALID_TEXT
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        return ErrorCode.E_EDIT_INVALID_TEXT
    if len(encoded) > MAX_EDIT_BYTES:
        return ErrorCode.E_EDIT_TOO_LARGE
    if _NUL in text:
        return ErrorCode.E_EDIT_INVALID_TEXT
    literal = TOKEN_RE.sub(_NUL, text)
    for char in literal:
        if not (" " <= char <= "~" or char in _ALLOWED_CONTROLS or char == _NUL):
            return ErrorCode.E_EDIT_INVALID_TEXT
    for word in _WORD_RE.findall(literal):
        if not _word_ok(word, terms):
            return ErrorCode.E_EDIT_INVALID_TEXT
    return None


def check_code_text(text: object, /) -> ErrorCode | None:
    """Agent text for a Python edit: size, then printable ASCII, TAB and newlines only.

    Words are not checked here: a Python word is public, a token or a new identifier, and the
    session resolves each one. Non-ASCII text cannot introduce a homoglyph of a public name.
    """
    if type(text) is not str:
        return ErrorCode.E_EDIT_INVALID_TEXT
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        return ErrorCode.E_EDIT_INVALID_TEXT
    if len(encoded) > MAX_EDIT_BYTES:
        return ErrorCode.E_EDIT_TOO_LARGE
    for char in text:
        if not (" " <= char <= "~" or char in _ALLOWED_CONTROLS):
            return ErrorCode.E_EDIT_INVALID_TEXT
    return None


def adapt_newlines(file_text: str, text: str, /) -> str:
    """Write text with the file's line ending when the file is all CRLF (no lone LF in it)."""
    if "\r\n" in file_text and "\n" not in file_text.replace("\r\n", ""):
        return text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text


def tokens_in(text: str, /) -> tuple[str, ...]:
    """Every token of the text, in order, repeats included."""
    return tuple(TOKEN_RE.findall(text))


def substitute(text: str, chosen: Mapping[str, str], /) -> str:
    """Replace each token by its chosen surface. Every token of text must be in chosen."""
    return TOKEN_RE.sub(lambda match: chosen[match.group(0)], text)


def count_overlapping(haystack: str, needle: str, /) -> tuple[int, int]:
    """Occurrences of needle in haystack, overlapping ones included, and the first index."""
    if not needle:
        return 0, -1
    count = 0
    first = -1
    position = haystack.find(needle)
    while position != -1:
        if count == 0:
            first = position
        count += 1
        position = haystack.find(needle, position + 1)
    return count, first


def choose(
    file_text: str, old: str, options: Mapping[str, frozenset[str]], /
) -> tuple[dict[str, str], int] | ErrorCode:
    """The one surface choice for the tokens of old that matches exactly once in the file.

    options maps each token of old to its candidate surfaces. Every choice is tried in sorted
    order (at most MAX_COMBINATIONS). No choice matching gives E_EDIT_NO_MATCH; more than one
    matching choice, or one matching twice, gives E_EDIT_AMBIGUOUS.
    """
    if not old:
        return ErrorCode.E_EDIT_INVALID_TEXT
    tokens = sorted(set(tokens_in(old)))
    combinations = 1
    for token in tokens:
        candidates = options.get(token)
        if not candidates:
            return ErrorCode.E_EDIT_UNKNOWN_TOKEN
        combinations *= len(candidates)
    if combinations > MAX_COMBINATIONS:
        return ErrorCode.E_TOKEN_AMBIGUOUS
    matches: list[tuple[dict[str, str], int, int]] = []
    ordered = [sorted(options[token]) for token in tokens]
    for picks in itertools.product(*ordered):
        chosen = dict(zip(tokens, picks, strict=True))
        count, first = count_overlapping(file_text, substitute(old, chosen))
        if count:
            matches.append((chosen, first, count))
    if not matches:
        return ErrorCode.E_EDIT_NO_MATCH
    if len(matches) > 1 or matches[0][2] > 1:
        return ErrorCode.E_EDIT_AMBIGUOUS
    chosen, first, _count = matches[0]
    return chosen, first
