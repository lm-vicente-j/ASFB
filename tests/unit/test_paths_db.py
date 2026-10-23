"""The database suffixes are document suffixes: the directory walk finds them."""

from pathlib import Path

from asfb.vault import paths


def test_the_three_database_suffixes_are_document_suffixes() -> None:
    assert {".db", ".sqlite", ".sqlite3"} <= paths.DOCUMENT_SUFFIXES


def test_the_walk_finds_database_files_in_any_case(tmp_path: Path) -> None:
    for name in ("a.db", "b.SQLITE", "c.sqlite3", "d.dbx"):
        (tmp_path / name).write_bytes(b"")
    found = paths.walk_documents(str(tmp_path))
    assert isinstance(found, list)
    assert sorted(Path(item).name for item in found) == ["a.db", "b.SQLITE", "c.sqlite3"]
