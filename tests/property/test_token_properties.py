import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.tokens import tokenize
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted

SHAPE_RE = r"t_[0-9a-f]{16}"
KEY_BYTES = st.binary(min_size=32, max_size=32)
VALUES = st.text(min_size=8, max_size=64)
SURROGATES = st.characters(min_codepoint=0xD800, max_codepoint=0xDFFF, exclude_categories=())
NON_TRIVIAL_VALUES = [f"value-{index}" for index in range(200)]


@given(KEY_BYTES, VALUES)
def test_same_key_and_value_give_same_token(key_bytes: bytes, value: str) -> None:
    first = tokenize(SessionKey(key_bytes), Untrusted(value))
    second = tokenize(SessionKey(key_bytes), Untrusted(value))
    assert first == second


def test_different_keys_give_different_tokens_for_value_set() -> None:
    key_a = SessionKey(bytes(32))
    key_b = SessionKey(b"\x01" * 32)
    tokens_a = [tokenize(key_a, Untrusted(value)) for value in NON_TRIVIAL_VALUES]
    tokens_b = [tokenize(key_b, Untrusted(value)) for value in NON_TRIVIAL_VALUES]
    assert len(set(tokens_a)) == len(NON_TRIVIAL_VALUES)
    assert all(a != b for a, b in zip(tokens_a, tokens_b, strict=True))


@given(KEY_BYTES, VALUES)
def test_token_always_matches_format(key_bytes: bytes, value: str) -> None:
    token = tokenize(SessionKey(key_bytes), Untrusted(value))
    assert re.fullmatch(SHAPE_RE, token) is not None


@given(KEY_BYTES, VALUES)
def test_token_never_contains_value(key_bytes: bytes, value: str) -> None:
    token = tokenize(SessionKey(key_bytes), Untrusted(value))
    assert value not in token


@given(st.text(alphabet=SURROGATES, min_size=1, max_size=8))
def test_lone_surrogates_raise_encoding_error(value: str) -> None:
    with pytest.raises(AsfbError) as info:
        tokenize(SessionKey(bytes(32)), Untrusted(value))
    assert info.value.code is ErrorCode.E_TOKEN_ENCODING
    assert str(info.value) == "E_TOKEN_ENCODING"
    assert info.value.__cause__ is None
    assert info.value.__context__ is None
