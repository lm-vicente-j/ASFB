"""Text properties: a canary anywhere in arbitrary text never reaches output, and every token of a
view is one of the pairs the sanitizer minted."""

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from asfb import ingest
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.text import censor_text_with_pairs
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted
from tests.canary.harness import find_leaks, new_canary, text_pipeline

_INPUT_CODES = frozenset({ErrorCode.E_TEXT_LINES, ErrorCode.E_SOURCE_SIZE})
_TOKEN_TEXT = st.text(alphabet=string.ascii_letters + " \t\r\né́​‮", max_size=120)
_KEYS = st.binary(min_size=32, max_size=32)


def _attempt(text: str, key: SessionKey) -> list[str] | ErrorCode:
    try:
        return text_pipeline(text, key)
    except AsfbError as error:
        return error.code


@settings(deadline=None, max_examples=200)
@given(before=_TOKEN_TEXT, after=_TOKEN_TEXT, key_bytes=_KEYS)
def test_canary_in_arbitrary_text_never_reaches_output(
    before: str, after: str, key_bytes: bytes
) -> None:
    canary = new_canary()
    outcome = _attempt(f"{before}\n{canary}\n{after}", SessionKey(key_bytes))
    if isinstance(outcome, ErrorCode):
        assert outcome in _INPUT_CODES
        return
    assert find_leaks(outcome, [canary]) == []


@settings(deadline=None, max_examples=200)
@given(text=_TOKEN_TEXT, key_bytes=_KEYS)
def test_view_tokens_are_a_subset_of_the_pairs(text: str, key_bytes: bytes) -> None:
    key = SessionKey(key_bytes)
    eol, lines = ingest.split_lines(text)
    model, pairs = censor_text_with_pairs(key, eol, lines, Profile.STRICT)
    minted = {token for token, _surface in pairs.items()}
    for paragraph in model.paragraphs:
        for line in paragraph.lines:
            assert line.text in minted
    for token, surface in pairs.items():
        assert token_for(key, Untrusted(surface)) == token
