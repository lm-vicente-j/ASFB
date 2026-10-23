"""The command line on a SQLite file: the censored view, the JSON document, the sibling WAL flag,
the header refusal and the listing of a directory that holds databases."""

import json
from pathlib import Path

import pytest

from asfb.cli import main
from tests.sqlite_images import SAMPLE_SCHEMA, image

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    root.mkdir()
    (root / "app.db").write_bytes(image(SAMPLE_SCHEMA))
    return root


def test_sanitize_database_prints_the_view_without_the_names(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["sanitize", str(site / "app.db")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("db page_size=b13 wal=no")
    assert "clientes" not in out
    assert "a@x" not in out


def test_sanitize_database_json_is_one_db_document(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["sanitize", str(site / "app.db"), "--json"]) == 0
    out = capsys.readouterr().out
    document = json.loads(out)
    assert document["schema_id"] == "asfb.db/1"
    assert "clientes" not in out
    assert "a@x" not in out


def test_a_sibling_wal_file_shows_in_the_view(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (site / "app.db-wal").write_bytes(b"not read")
    assert main(["sanitize", str(site / "app.db")]) == 0
    assert "wal=yes" in capsys.readouterr().out


def test_a_directory_with_a_database_lists_it(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["sanitize", str(site)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("d_")
    assert "clientes" not in out


def test_a_png_named_db_exits_one_with_only_the_code(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (site / "fake.db").write_bytes(PNG)
    assert main(["sanitize", str(site / "fake.db")]) == 1
    captured = capsys.readouterr()
    assert captured.err == "E_DB_NOT_SQLITE\n"
    assert captured.out == ""
