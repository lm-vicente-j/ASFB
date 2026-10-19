import re
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.tokens import tokenize
from asfb.types.base import StrictModel
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted

VALID = "t_0123456789abcdef"
WRONG_STRINGS = [
    "t_0123456789ABCDEF",
    "t_0123456789abcde",
    "t_0123456789abcdef0",
    "t_0123456789abcdef\n",
    "T_0123456789abcdef",
    "x_0123456789abcdef",
    "t_0123456789abcdeg",
    "",
]


class TextSub(str):
    """A str subclass with a valid token body."""


class TokenHolder(StrictModel):
    tok: Token


class LaxTokenHolder(BaseModel):
    model_config = ConfigDict(strict=False)
    tok: Token


def test_accepts_valid_token() -> None:
    token = Token(VALID)
    assert isinstance(token, Token)
    assert token == VALID
    assert re.fullmatch(r"t_[0-9a-f]{16}", token) is not None


@pytest.mark.parametrize("value", WRONG_STRINGS)
def test_rejects_wrong_strings(value: str) -> None:
    with pytest.raises(ValueError, match="invalid token format"):
        Token(value)


def test_rejects_str_subclass_and_bytes() -> None:
    raw: Any = VALID.encode()
    with pytest.raises(TypeError, match="exact str"):
        Token(TextSub(VALID))
    with pytest.raises(TypeError, match="exact str"):
        Token(raw)


def test_pydantic_accepts_valid_and_token_instances() -> None:
    assert TokenHolder.model_validate({"tok": VALID}).tok == VALID
    assert TokenHolder.model_validate({"tok": Token(VALID)}).tok == VALID


@pytest.mark.parametrize("value", WRONG_STRINGS)
def test_pydantic_rejects_wrong_strings(value: str) -> None:
    with pytest.raises(ValidationError):
        TokenHolder.model_validate({"tok": value})


def test_pydantic_rejects_non_str() -> None:
    with pytest.raises(ValidationError):
        TokenHolder.model_validate({"tok": 5})


def test_json_schema_pattern() -> None:
    schema = TokenHolder.model_json_schema()
    assert schema["properties"]["tok"]["pattern"] == r"^t_[0-9a-f]{16}$"


def test_lax_host_model_rejects_bytes() -> None:
    raw: Any = VALID.encode()
    with pytest.raises(ValidationError):
        LaxTokenHolder.model_validate({"tok": raw})


def test_lax_host_model_accepts_str() -> None:
    assert LaxTokenHolder.model_validate({"tok": VALID}).tok == VALID


def test_tokenize_refuses_non_str_without_content() -> None:
    canary = "cnry_0123456789abcdef"
    raw: Any = canary.encode()
    with pytest.raises(AsfbError) as info:
        tokenize(SessionKey(bytes(32)), Untrusted(raw))
    error = info.value
    assert error.code is ErrorCode.E_TOKEN_ENCODING
    assert error.args == ("E_TOKEN_ENCODING",)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert canary not in repr(error)
