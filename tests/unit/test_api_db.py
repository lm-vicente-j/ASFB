"""api.sanitize on SQLite files: the three suffixes, the header refusal and the worker always."""

import json
from pathlib import Path

import pytest

from asfb import api
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import db as ingest_db
from asfb.schema.db import DbV1
from asfb.types.key import SessionKey
from tests.sqlite_images import SAMPLE_SCHEMA, image

KEY = SessionKey(bytes(range(32)))


@pytest.mark.parametrize("name", ["app.db", "app.sqlite", "app.SQLITE3"])
def test_each_database_suffix_is_censored(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(image(SAMPLE_SCHEMA))
    doc = api.sanitize(str(path), key=KEY)
    assert isinstance(doc.model, DbV1)
    assert json.loads(doc.json)["schema_id"] == "asfb.db/1"
    assert str(doc.views.censored()).startswith("db page_size=b13 wal=no")


def test_isolate_true_and_false_give_the_same_document(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    path.write_bytes(image(SAMPLE_SCHEMA))
    in_process = api.sanitize(str(path), key=KEY, isolate=False)
    isolated = api.sanitize(str(path), key=KEY, isolate=True)
    assert in_process.json == isolated.json


def test_a_png_named_db_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "fake.db"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    with pytest.raises(AsfbError) as info:
        api.sanitize(str(path), key=KEY)
    assert info.value.code is ErrorCode.E_DB_NOT_SQLITE


def test_the_wal_flag_follows_the_sibling_file(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    path.write_bytes(image(SAMPLE_SCHEMA))
    (tmp_path / "app.db-wal").write_bytes(b"x")
    assert '"wal_present":true' in api.sanitize(str(path), key=KEY).json


def test_a_database_is_never_parsed_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "app.db"
    path.write_bytes(image(SAMPLE_SCHEMA))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the in-process pipeline must not read a database")

    monkeypatch.setattr(ingest_db, "read_bytes", forbidden)
    doc = api.sanitize(str(path), key=KEY, isolate=False)
    assert isinstance(doc.model, DbV1)
