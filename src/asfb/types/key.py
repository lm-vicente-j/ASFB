"""Per-session HMAC key. The key bytes are never handed out."""

import hashlib
import hmac
import secrets
from typing import NoReturn, Self, SupportsIndex, final

_KEY_SIZE = 32


@final
class SessionKey:
    """32-byte key held in memory only."""

    __slots__ = ("_key",)
    _key: bytes

    def __init__(self, key: bytes, /) -> None:
        if type(key) is not bytes or len(key) != _KEY_SIZE:
            raise ValueError("session key must be 32 bytes")
        if hasattr(self, "_key"):
            raise TypeError("SessionKey is immutable")
        object.__setattr__(self, "_key", key)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("SessionKey cannot be subclassed")

    def __setattr__(self, name: str, value: object, /) -> None:
        raise TypeError("SessionKey is immutable")

    def __delattr__(self, name: str, /) -> None:
        raise TypeError("SessionKey is immutable")

    @classmethod
    def generate(cls) -> Self:
        return cls(secrets.token_bytes(_KEY_SIZE))

    def digest(self, msg: bytes) -> bytes:
        return hmac.new(self._key, msg, hashlib.sha256).digest()

    def __repr__(self) -> str:
        return "SessionKey(<redacted>)"

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> NoReturn:
        raise TypeError("SessionKey cannot be pickled")
