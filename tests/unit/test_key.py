import hashlib
import hmac
import pickle
from typing import Any

import pytest

from asfb.types.key import SessionKey

KEY_BYTES = bytes(range(32))


@pytest.mark.parametrize("size", [0, 16, 31, 33, 64])
def test_rejects_wrong_size(size: int) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        SessionKey(bytes(size))


def test_accepts_32_bytes() -> None:
    assert (
        SessionKey(KEY_BYTES).digest(b"msg") == hmac.new(KEY_BYTES, b"msg", hashlib.sha256).digest()
    )


def test_generate_gives_different_keys() -> None:
    first = SessionKey.generate()
    second = SessionKey.generate()
    assert first.digest(b"msg") != second.digest(b"msg")


def test_repr_is_redacted() -> None:
    rendered = repr(SessionKey(KEY_BYTES))
    assert rendered == "SessionKey(<redacted>)"
    assert KEY_BYTES.hex() not in rendered


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_pickling_raises(protocol: int) -> None:
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(SessionKey(KEY_BYTES), protocol=protocol)


def test_no_public_attribute_exposes_key_bytes() -> None:
    key = SessionKey(KEY_BYTES)
    public = {name: getattr(key, name) for name in dir(key) if not name.startswith("_")}
    assert KEY_BYTES not in public.values()


def test_reassigning_key_raises() -> None:
    key = SessionKey(KEY_BYTES)
    name = "_key"
    with pytest.raises(TypeError, match="immutable"):
        setattr(key, name, bytes(32))
    assert key.digest(b"msg") == hmac.new(KEY_BYTES, b"msg", hashlib.sha256).digest()


def test_deleting_key_raises() -> None:
    key = SessionKey(KEY_BYTES)
    name = "_key"
    with pytest.raises(TypeError, match="immutable"):
        delattr(key, name)


def test_reinitialization_raises() -> None:
    key = SessionKey(KEY_BYTES)
    with pytest.raises(TypeError, match="immutable"):
        key.__init__(bytes(32))
    assert key.digest(b"msg") == hmac.new(KEY_BYTES, b"msg", hashlib.sha256).digest()


def test_subclassing_raises() -> None:
    base: Any = SessionKey
    with pytest.raises(TypeError, match="subclassed"):
        type("Sub", (base,), {})
