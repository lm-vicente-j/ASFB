"""Mint tokens from untrusted text."""

from asfb import normalize
from asfb.errors import AsfbError, ErrorCode
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted, unwrap

_DOMAIN = b"asfb.token/1\x00"


def _encode(text: object) -> bytes | None:
    if type(text) is not str:
        return None
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        return None


def tokenize(key: SessionKey, value: Untrusted[str]) -> Token:
    data = _encode(unwrap(value))
    if data is None:
        raise AsfbError(ErrorCode.E_TOKEN_ENCODING)
    return Token("t_" + key.digest(_DOMAIN + data)[:8].hex())


def token_for(key: SessionKey, u: Untrusted[str], /) -> Token:
    return tokenize(key, normalize.value(u))
