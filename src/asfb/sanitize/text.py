"""Plain text censoring: each non-blank line is one token, and blank lines split the paragraphs.

A line is blank when normalize.value leaves nothing, which includes lines of zero-width characters
only. Indentation is the leading spaces plus four per tab before the first other character, capped
at 64 (only the width is kept, never the characters). The pair surface is the raw line with outer
ASCII whitespace stripped, as in the HTML sanitizer. Pairs go only to the vault.
"""

from typing import Final

from asfb import normalize
from asfb.ingest.text import Eol
from asfb.sanitize.pairs import PairRecorder, SurfacePairs, surface_of
from asfb.sanitize.tokens import token_for
from asfb.schema import text as schema
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted, unwrap

_TAB_COLUMNS: Final = 4


def _indent(raw: str) -> int:
    columns = 0
    for char in raw:
        if char == " ":
            columns += 1
        elif char == "\t":
            columns += _TAB_COLUMNS
        else:
            break
    return min(columns, schema.MAX_INDENT)


def censor_text_with_pairs(
    key: SessionKey, eol: Eol, lines: tuple[Untrusted[str], ...], profile: Profile, /
) -> tuple[schema.TextV1, SurfacePairs]:
    """Censor split lines into the v1 text model and the (token, surface) pairs it minted."""
    recorder = PairRecorder()
    paragraphs: list[schema.TextParagraph] = []
    current: list[schema.TextLine] = []
    for line in lines:
        if not unwrap(normalize.value(line)):
            if current:
                paragraphs.append(schema.TextParagraph(lines=tuple(current)))
                current = []
            continue
        raw = unwrap(line)
        token = token_for(key, line)
        recorder.record(token, surface_of(raw))
        current.append(schema.TextLine(indent=_indent(raw), text=token))
    if current:
        paragraphs.append(schema.TextParagraph(lines=tuple(current)))
    model = schema.TextV1(profile=profile, vocab=(), eol=eol, paragraphs=tuple(paragraphs))
    return model, SurfacePairs(recorder.items())
