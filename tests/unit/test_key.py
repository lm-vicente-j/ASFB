import hashlib
import hmac
import os
import pickle
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from asfb.errors import AsfbError, ErrorCode
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


def test_export_returns_the_32_key_bytes() -> None:
    exported = SessionKey(KEY_BYTES).export()
    assert exported == KEY_BYTES
    assert type(exported) is bytes
    assert len(exported) == 32


def test_export_does_not_change_repr_or_pickling() -> None:
    key = SessionKey(KEY_BYTES)
    key.export()
    assert repr(key) == "SessionKey(<redacted>)"
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(key)


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


POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX only")
HEX = "0123456789abcdef" * 4


def _key_file(tmp_path: Path, content: bytes, name: str = "key.txt") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    if os.name == "posix":
        os.chmod(path, 0o600)  # a umask-default file is group readable and is refused
    return path


def test_create_then_load_gives_the_same_key(tmp_path: Path) -> None:
    path = tmp_path / "key.txt"
    created = SessionKey.create_file(path)
    loaded = SessionKey.from_file(path)
    assert loaded.digest(b"msg") == created.digest(b"msg")
    assert path.read_bytes() == created.export().hex().encode("ascii") + b"\n"


def test_created_files_are_different_keys(tmp_path: Path) -> None:
    first = SessionKey.create_file(tmp_path / "a.key")
    second = SessionKey.create_file(tmp_path / "b.key")
    assert first.digest(b"msg") != second.digest(b"msg")


def test_create_refuses_an_existing_path_and_keeps_it(tmp_path: Path) -> None:
    path = _key_file(tmp_path, b"keep me\n")
    with pytest.raises(AsfbError) as info:
        SessionKey.create_file(path)
    assert info.value.code is ErrorCode.E_KEY_FILE
    assert path.read_bytes() == b"keep me\n"


def test_key_file_errors_carry_only_the_code(tmp_path: Path) -> None:
    path = tmp_path / "secret-location-name.key"
    with pytest.raises(AsfbError) as info:
        SessionKey.from_file(path)
    assert info.value.code is ErrorCode.E_KEY_FILE
    assert str(info.value) == "E_KEY_FILE"
    assert repr(info.value) == "AsfbError('E_KEY_FILE')"
    assert "secret-location-name" not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


def test_missing_file_is_a_key_file_error(tmp_path: Path) -> None:
    with pytest.raises(AsfbError) as info:
        SessionKey.from_file(tmp_path / "missing.key")
    assert info.value.code is ErrorCode.E_KEY_FILE


def test_directory_is_a_key_file_error(tmp_path: Path) -> None:
    with pytest.raises(AsfbError) as info:
        SessionKey.from_file(tmp_path)
    assert info.value.code is ErrorCode.E_KEY_FILE


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(f"{HEX}\n".encode(), id="lf"),
        pytest.param(f"{HEX}\r\n".encode(), id="crlf"),
        pytest.param(HEX.encode(), id="no-newline"),
    ],
)
def test_accepts_the_documented_line_endings(tmp_path: Path, content: bytes) -> None:
    path = _key_file(tmp_path, content)
    assert SessionKey.from_file(path).export() == bytes.fromhex(HEX)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"", id="empty"),
        pytest.param(HEX[:-1].encode() + b"\n", id="short"),
        pytest.param((HEX + "0").encode() + b"\n", id="long"),
        pytest.param(HEX.upper().encode() + b"\n", id="uppercase"),
        pytest.param(("g" + HEX[1:]).encode() + b"\n", id="not-hex"),
        pytest.param(HEX.encode() + b"\n\n", id="two-newlines"),
        pytest.param(HEX.encode() + b"\r", id="lone-cr"),
        pytest.param(HEX.encode() + b" \n", id="trailing-space"),
        pytest.param(b" " + HEX.encode() + b"\n", id="leading-space"),
        pytest.param(HEX.encode() + b"\nextra", id="trailing-text"),
        pytest.param(b"\xff" * 64, id="binary"),
        pytest.param(b"x" * 1000, id="oversized"),
    ],
)
def test_malformed_key_file_is_refused(tmp_path: Path, content: bytes) -> None:
    path = _key_file(tmp_path, content)
    with pytest.raises(AsfbError) as info:
        SessionKey.from_file(path)
    assert info.value.code is ErrorCode.E_KEY_FILE


@POSIX_ONLY
def test_group_or_world_readable_key_file_is_refused(tmp_path: Path) -> None:
    path = _key_file(tmp_path, f"{HEX}\n".encode())
    for mode in (0o644, 0o640, 0o604):
        os.chmod(path, mode)
        with pytest.raises(AsfbError) as info:
            SessionKey.from_file(path)
        assert info.value.code is ErrorCode.E_KEY_FILE
    os.chmod(path, 0o600)
    assert SessionKey.from_file(path).export() == bytes.fromhex(HEX)


@POSIX_ONLY
def test_created_key_file_is_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "key.txt"
    SessionKey.create_file(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.skipif(sys.platform != "win32", reason="Windows does not check POSIX modes")
def test_windows_loads_a_key_file_without_a_mode_check(tmp_path: Path) -> None:
    path = tmp_path / "key.txt"
    path.write_bytes(f"{HEX}\n".encode())
    assert SessionKey.from_file(path).export() == bytes.fromhex(HEX)
