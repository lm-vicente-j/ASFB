"""Client-side SQLite checks: header, size and the WAL flag. The WAL file is never read."""

import sqlite3
from pathlib import Path

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import db as ingest_db
from asfb.ingest import limits

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE clientes (id INTEGER PRIMARY KEY, email TEXT)")
        connection.commit()
    finally:
        connection.close()
    return path


def test_a_real_database_is_read_with_no_wal(tmp_path: Path) -> None:
    path = _database(tmp_path / "app.db")
    found = ingest_db.read_file(str(path))
    assert isinstance(found, ingest_db.DbFile)
    assert found.data.startswith(ingest_db.HEADER)
    assert found.data == path.read_bytes()
    assert found.wal_present is False


def test_a_png_renamed_to_db_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "picture.db"
    path.write_bytes(PNG)
    assert ingest_db.read_file(str(path)) is ErrorCode.E_DB_NOT_SQLITE
    with pytest.raises(AsfbError) as info:
        ingest_db.check_file(str(path))
    assert info.value.code is ErrorCode.E_DB_NOT_SQLITE


@pytest.mark.parametrize(
    "content", [b"", b"SQLite format 3", b"SQLite format 2\x00" + b"\x00" * 80]
)
def test_a_short_or_other_header_is_refused(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "short.sqlite"
    path.write_bytes(content)
    assert ingest_db.read_file(str(path)) is ErrorCode.E_DB_NOT_SQLITE


def test_a_file_over_the_limit_is_refused_by_size(tmp_path: Path) -> None:
    path = tmp_path / "big.db"
    with path.open("wb") as handle:
        handle.write(ingest_db.HEADER)
        handle.truncate(limits.MAX_DB_BYTES + 1)
    assert ingest_db.read_file(str(path)) is ErrorCode.E_DB_TOO_LARGE


def test_a_file_at_the_limit_is_read(tmp_path: Path) -> None:
    path = tmp_path / "edge.db"
    with path.open("wb") as handle:
        handle.write(ingest_db.HEADER)
        handle.truncate(limits.MAX_DB_BYTES)
    found = ingest_db.read_file(str(path))
    assert isinstance(found, ingest_db.DbFile)
    assert len(found.data) == limits.MAX_DB_BYTES


def test_a_sibling_wal_file_is_recorded_and_not_read(tmp_path: Path) -> None:
    path = _database(tmp_path / "app.sqlite3")
    wal = Path(f"{path}-wal")
    wal.write_bytes(b"not a wal at all")
    found = ingest_db.read_file(str(path))
    assert isinstance(found, ingest_db.DbFile)
    assert found.wal_present is True
    assert found.data == path.read_bytes()
    assert wal.read_bytes() == b"not a wal at all"


def test_a_missing_or_unreadable_path_is_a_read_error(tmp_path: Path) -> None:
    assert ingest_db.read_file(str(tmp_path / "nope.db")) is ErrorCode.E_SOURCE_READ
    assert ingest_db.read_file(str(tmp_path)) is ErrorCode.E_SOURCE_READ


def test_a_path_object_is_not_supported() -> None:
    assert ingest_db.read_file(None) is ErrorCode.E_SOURCE_UNSUPPORTED  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.db", True), ("A.SQLITE", True), ("b.sqlite3", True), ("c.dbx", False), ("db", False)],
)
def test_database_suffixes(name: str, expected: bool) -> None:
    assert ingest_db.is_database_name(name) is expected
