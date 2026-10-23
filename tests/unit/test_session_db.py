"""A SQLite file through Session: header refusal, WAL flag, listing, edit refusal, schema diff."""

import json
import sqlite3
from pathlib import Path

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.session import Listing, Sanitized, Session
from asfb.types.key import SessionKey
from tests.sqlite_images import SAMPLE_SCHEMA, image

KEY = SessionKey(bytes(range(32)))
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _write_database(path: Path, script: str = SAMPLE_SCHEMA) -> Path:
    path.write_bytes(image(script))
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    _write_database(directory / "app.db")
    _write_database(directory / "copy.sqlite3")
    return directory


@pytest.fixture
def session(root: Path) -> Session:
    return Session([str(root)], key=KEY, isolate=False)


def _sanitized(session: Session, name: str) -> Sanitized:
    result = session.sanitize(name)
    assert isinstance(result, Sanitized)
    return result


@pytest.mark.parametrize("name", ["app.db", "copy.sqlite3"])
def test_a_database_file_is_censored_into_a_db_model(session: Session, name: str) -> None:
    result = _sanitized(session, name)
    assert '"schema_id":"asfb.db/1"' in result.json
    assert str(result.view).startswith("db page_size=b13 wal=no")
    assert "clientes" not in str(result.view)


def test_a_png_named_db_is_refused_by_its_header(session: Session, root: Path) -> None:
    (root / "fake.db").write_bytes(PNG)
    with pytest.raises(AsfbError) as info:
        session.sanitize("fake.db")
    assert info.value.code is ErrorCode.E_DB_NOT_SQLITE


def test_an_oversized_file_is_refused_before_its_bytes_are_read(
    session: Session, root: Path
) -> None:
    with (root / "huge.db").open("wb") as handle:
        handle.write(b"SQLite format 3\x00")
        handle.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(AsfbError) as info:
        session.sanitize("huge.db")
    assert info.value.code is ErrorCode.E_DB_TOO_LARGE


def test_a_sibling_wal_file_is_recorded_as_present(session: Session, root: Path) -> None:
    before = _sanitized(session, "app.db")
    (root / "app.db-wal").write_bytes(b"not read")
    after = _sanitized(session, "app.db")
    assert '"wal_present":true' in after.json
    assert '"wal_present":false' in before.json
    assert after.ref.revision == before.ref.revision + 1


def test_the_same_bytes_give_the_same_revision(session: Session) -> None:
    first = _sanitized(session, "app.db")
    second = _sanitized(session, "app.db")
    assert first.ref == second.ref


def test_a_database_cannot_be_edited(session: Session) -> None:
    stored = _sanitized(session, "app.db")
    with pytest.raises(AsfbError) as info:
        session.edit(str(stored.ref), "x", "y")
    assert info.value.code is ErrorCode.E_EDIT_UNSUPPORTED_KIND


def test_the_listing_names_database_files_by_their_extension(session: Session) -> None:
    listed = session.sanitize(".")
    assert isinstance(listed, Listing)
    extensions = sorted(entry["ext"] for entry in json.loads(listed.json)["entries"])
    assert extensions == ["db", "sqlite3"]


def test_a_database_reached_by_its_token_path_is_censored(session: Session) -> None:
    listed = session.sanitize(".")
    assert isinstance(listed, Listing)
    entry = next(row for row in json.loads(listed.json)["entries"] if row["ext"] == "db")
    # The listing gives the stem token and the extension apart; a token path joins them.
    last = f"{entry['path'][-1]}.{entry['ext']}"
    stem = "/".join([*entry["path"][:-1], last])
    result = session.sanitize(stem)
    assert isinstance(result, Sanitized)
    assert '"schema_id":"asfb.db/1"' in result.json


def test_a_schema_diff_of_a_copy_with_one_new_column_has_one_insert(
    session: Session, root: Path
) -> None:
    before = _sanitized(session, "app.db")
    connection = sqlite3.connect(root / "copy.sqlite3")
    try:
        connection.execute("ALTER TABLE clientes ADD COLUMN telefono TEXT")
        connection.commit()
    finally:
        connection.close()
    after = _sanitized(session, "copy.sqlite3")
    diffed = session.diff(str(before.ref), str(after.ref))
    view = str(diffed.view).splitlines()
    added = [line for line in view if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in view if line.startswith("-") and not line.startswith("---")]
    assert len(added) == 1
    assert added[0].startswith("+  column ")
    assert removed == []
    assert len([line for line in view if line.startswith("@@")]) == 1
    before_lines = set(str(before.view).splitlines())
    new_lines = [line for line in str(after.view).splitlines() if line not in before_lines]
    assert new_lines == [added[0][1:]]


def test_a_database_diffed_with_a_text_file_falls_back_to_lines(
    session: Session, root: Path
) -> None:
    (root / "notes.txt").write_text("hola\n", encoding="utf-8")
    database = _sanitized(session, "app.db")
    text = _sanitized(session, "notes.txt")
    diffed = session.diff(str(database.ref), str(text.ref))
    assert str(diffed.view).startswith("--- d_")
