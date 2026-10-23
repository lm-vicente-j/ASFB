"""Per-session HMAC key. The key bytes are never handed out."""

import hashlib
import hmac
import os
import re
import secrets
import stat
from contextlib import suppress
from typing import Final, NoReturn, Self, SupportsIndex, final

from asfb.errors import AsfbError, ErrorCode

_KEY_SIZE = 32
_KEY_FILE_MAX: Final = 67  # 64 lowercase hex digits and an optional CRLF
_KEY_FILE_RE: Final = re.compile(rb"[0-9a-f]{64}(?:\r?\n)?")


def _read_key_bytes(path: str | os.PathLike[str]) -> bytes | ErrorCode:
    """The 32 key bytes of a key file. POSIX files must not be readable by group or others."""
    try:
        with open(path, "rb") as handle:
            if os.name == "posix" and stat.S_IMODE(os.fstat(handle.fileno()).st_mode) & 0o077:
                return ErrorCode.E_KEY_FILE
            data = handle.read(_KEY_FILE_MAX + 1)
    except (OSError, TypeError, ValueError):
        return ErrorCode.E_KEY_FILE
    if len(data) > _KEY_FILE_MAX or _KEY_FILE_RE.fullmatch(data) is None:
        return ErrorCode.E_KEY_FILE
    return bytes.fromhex(data[: _KEY_SIZE * 2].decode("ascii"))


def _write_key_file(path: str | os.PathLike[str], key: "SessionKey") -> ErrorCode | None:
    """Create a new key file with mode 0600. An existing file is never overwritten."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except (OSError, TypeError, ValueError):
        return ErrorCode.E_KEY_FILE
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(key.export().hex().encode("ascii") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        with suppress(OSError):
            os.remove(path)
        return ErrorCode.E_KEY_FILE
    return None


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

    @classmethod
    def from_file(cls, path: str | os.PathLike[str], /) -> Self:
        """Load a key file written by create_file. Any failure raises E_KEY_FILE."""
        outcome = _read_key_bytes(path)
        if isinstance(outcome, ErrorCode):
            raise AsfbError(outcome)
        return cls(outcome)

    @classmethod
    def create_file(cls, path: str | os.PathLike[str], /) -> Self:
        """Generate a key and write it to a new file. An existing path raises E_KEY_FILE."""
        key = cls.generate()
        outcome = _write_key_file(path, key)
        if outcome is not None:
            raise AsfbError(outcome)
        return key

    def digest(self, msg: bytes) -> bytes:
        return hmac.new(self._key, msg, hashlib.sha256).digest()

    def export(self) -> bytes:
        """The raw key bytes. Callable only from asfb/sandbox/ (scanner rule)."""
        return self._key

    def __repr__(self) -> str:
        return "SessionKey(<redacted>)"

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> NoReturn:
        raise TypeError("SessionKey cannot be pickled")
