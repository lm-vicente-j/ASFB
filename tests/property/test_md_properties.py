"""Markdown properties: a canary inside arbitrary markdown-like text never reaches output, and the
egress gate never refuses a sanitizer output (only the input limits may refuse)."""

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from asfb.errors import AsfbError, ErrorCode
from asfb.types.key import SessionKey
from tests.canary.harness import find_leaks, md_pipeline, new_canary

_STRUCTURE = "#>-*_|[]()!<>`~:"
_INPUT_CODES = frozenset(
    {ErrorCode.E_MD_DEPTH, ErrorCode.E_MD_BLOCKS, ErrorCode.E_TEXT_LINES, ErrorCode.E_SOURCE_SIZE}
)
_MD_TEXT = st.text(alphabet=string.ascii_letters + " \n\t\\" + _STRUCTURE, max_size=160)
_KEYS = st.binary(min_size=32, max_size=32)


def _attempt(text: str, key: SessionKey) -> list[str] | ErrorCode:
    try:
        return md_pipeline(text, key)
    except AsfbError as error:
        return error.code


@settings(deadline=None, max_examples=200)
@given(before=_MD_TEXT, after=_MD_TEXT, key_bytes=_KEYS)
def test_canary_in_markdown_like_text_never_reaches_output(
    before: str, after: str, key_bytes: bytes
) -> None:
    canary = new_canary()
    outcome = _attempt(f"{before}\n{canary}\n{after}", SessionKey(key_bytes))
    if isinstance(outcome, ErrorCode):
        assert outcome in _INPUT_CODES
        return
    assert find_leaks(outcome, [canary]) == []
