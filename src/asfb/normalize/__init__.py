"""Normalization of untrusted text. It strips and never rejects.

Whitespace means ASCII whitespace (space, TAB, LF, FF, CR) for both the outer strip and split.
"""

import re
import unicodedata
from typing import Final

from asfb.types.untrusted import Untrusted, unwrap

_KEEP_CONTROLS: Final = frozenset("\t\n\r")
_ASCII_WHITESPACE: Final = " \t\n\f\r"
_SPLIT_RE: Final = re.compile(r"[ \t\n\f\r]+")


def _dropped(char: str) -> bool:
    category = unicodedata.category(char)
    if category == "Cf":
        return True
    return category == "Cc" and char not in _KEEP_CONTROLS


def value(u: Untrusted[str], /) -> Untrusted[str]:
    """Drop Cf and Cc controls, fold CR and CRLF to LF, apply NFC, strip outer whitespace."""
    kept = "".join(char for char in unwrap(u) if not _dropped(char))
    lines = kept.replace("\r\n", "\n").replace("\r", "\n")
    composed = unicodedata.normalize("NFC", lines)
    return Untrusted(composed.strip(_ASCII_WHITESPACE))


_CODE_KEEP: Final = frozenset("\t\n\r\f")


def _code_dropped(char: str) -> bool:
    if char in _CODE_KEEP:
        return False
    category = unicodedata.category(char)
    return category == "Cf" or category == "Cc"


def code_text(text: str, /) -> tuple[str, int]:
    """Python source: drop Cf and Cc except TAB, LF, CR and FF; fold CRLF and CR to LF.

    Returns the text and how many characters were dropped. Plain str in and out, on purpose:
    the only caller is the lexer in ingest/code.py, which feeds the stdlib tokenizer with it and
    returns offsets, never text. Nothing here decodes or re-encodes.
    """
    normalized, _spans = code_spans(text)
    dropped = sum(1 for char in text if _code_dropped(char))
    return normalized, dropped


def code_spans(text: str, /) -> tuple[str, tuple[tuple[int, int], ...]]:
    """code_text with the raw span of each output character.

    Character i of the normalized text came from text[start:stop] of the raw text, the span
    given as the pair (start, stop). A CRLF pair is one output LF with the span of both
    characters; a lone CR is one LF. Dropped characters belong to no span.
    """
    kept = [index for index, char in enumerate(text) if not _code_dropped(char)]
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    position = 0
    while position < len(kept):
        index = kept[position]
        if text[index] != "\r":
            out.append(text[index])
            spans.append((index, index + 1))
            position += 1
            continue
        out.append("\n")
        if position + 1 < len(kept) and text[kept[position + 1]] == "\n":
            spans.append((index, kept[position + 1] + 1))
            position += 2
        else:
            spans.append((index, index + 1))
            position += 1
    return "".join(out), tuple(spans)


def code_raw_range(
    raw: str, spans: tuple[tuple[int, int], ...], start: int, end: int, /
) -> tuple[int, int] | None:
    """The raw range [low, high) of the normalized range [start, end), or None.

    None when the range is empty or out of bounds, when the raw character just before low or
    just at high is a dropped control character (the range touches it), or when a dropped
    character lies inside the range (the range splits it). The caller refuses in those cases.
    """
    if not 0 <= start < end <= len(spans):
        return None
    low = spans[start][0]
    high = spans[end - 1][1]
    if low > 0 and _code_dropped(raw[low - 1]):
        return None
    if high < len(raw) and _code_dropped(raw[high]):
        return None
    if any(_code_dropped(char) for char in raw[low:high]):
        return None
    return low, high


def split(u: Untrusted[str], /) -> tuple[Untrusted[str], ...]:
    """Split on runs of ASCII whitespace. Callers pass the output of value()."""
    return tuple(Untrusted(part) for part in _SPLIT_RE.split(unwrap(u)) if part)
