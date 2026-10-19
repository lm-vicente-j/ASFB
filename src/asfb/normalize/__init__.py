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


def split(u: Untrusted[str], /) -> tuple[Untrusted[str], ...]:
    """Split on runs of ASCII whitespace. Callers pass the output of value()."""
    return tuple(Untrusted(part) for part in _SPLIT_RE.split(unwrap(u)) if part)
