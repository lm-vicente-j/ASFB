"""The hardened SQLite reader: schema and counts, refusals, budget, limits and no side files.

The tests reach the reader's private guards and budget on purpose; the SQL in them is fixed text.
"""

# ruff: noqa: SLF001, S608

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Final, Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import db
from asfb.ingest import limits as limits_module
from asfb.types.untrusted import unwrap

SCHEMA = """
CREATE TABLE clientes (
  id INTEGER PRIMARY KEY,
  "we""ird name" TEXT NOT NULL DEFAULT 'x',
  email VARCHAR(255) UNIQUE,
  ref INT REFERENCES clientes(id)
);
CREATE INDEX idx_email ON clientes(email);
CREATE INDEX idx_expr ON clientes(lower(email));
CREATE VIEW vista AS SELECT id FROM clientes;
CREATE TRIGGER tr AFTER INSERT ON clientes BEGIN INSERT INTO log_rows VALUES (new.id); END;
CREATE TABLE log_rows (n INTEGER);
CREATE TABLE sin_fila (a TEXT PRIMARY KEY, b) WITHOUT ROWID;
CREATE TABLE gen (a INT, b INT GENERATED ALWAYS AS (a * 2) VIRTUAL);
CREATE VIRTUAL TABLE busqueda USING fts5(texto);
INSERT INTO clientes(email) VALUES ('a@x'), ('b@x');
"""


def _image(script: str) -> bytes:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.executescript(script)
        return bytes(connection.serialize())
    finally:
        connection.close()


@pytest.fixture(scope="module")
def sample() -> db.DbSource:
    return db.read_bytes(_image(SCHEMA))


def _table(source: db.DbSource, name: str) -> db.DbTable:
    (table,) = [table for table in source.tables if unwrap(table.name) == name]
    return table


def test_tables_views_and_triggers_are_listed(sample: db.DbSource) -> None:
    names = [unwrap(table.name) for table in sample.tables]
    assert {"clientes", "log_rows", "sin_fila", "gen", "busqueda"} <= set(names)
    assert [unwrap(view) for view in sample.views] == ["vista"]
    (trigger,) = sample.triggers
    assert (unwrap(trigger.name), unwrap(trigger.table)) == ("tr", "clientes")


def test_page_size_is_read_from_the_header(sample: db.DbSource) -> None:
    assert sample.page_size == 4096


def test_row_counts_are_exact_for_plain_tables(sample: db.DbSource) -> None:
    assert _table(sample, "clientes").rows == 2
    assert _table(sample, "log_rows").rows == 2
    assert _table(sample, "sin_fila").rows == 0


def test_a_virtual_table_is_not_connected_and_has_no_known_count(sample: db.DbSource) -> None:
    virtual = _table(sample, "busqueda")
    assert virtual.rows is None
    assert virtual.columns == ()
    assert virtual.indexes == ()


def test_columns_carry_type_flags_default_and_key_position(sample: db.DbSource) -> None:
    clientes = _table(sample, "clientes")
    by_name = {unwrap(column.name): column for column in clientes.columns}
    assert by_name["id"].declared is not None
    assert unwrap(by_name["id"].declared) == "INTEGER"
    assert by_name["id"].pk == 1
    weird = by_name['we"ird name']
    assert weird.notnull is True
    assert weird.has_default is True
    assert weird.declared is not None
    assert unwrap(weird.declared) == "TEXT"
    assert by_name["email"].notnull is False
    assert by_name["email"].has_default is False
    assert by_name["email"].declared is not None
    assert unwrap(by_name["email"].declared) == "VARCHAR(255)"


def test_foreign_keys_name_the_target_table_and_column(sample: db.DbSource) -> None:
    ref = {unwrap(column.name): column for column in _table(sample, "clientes").columns}["ref"]
    (link,) = ref.foreign_keys
    assert unwrap(link.table) == "clientes"
    assert link.column is not None
    assert unwrap(link.column) == "id"


def test_a_generated_column_is_flagged_as_generated(sample: db.DbSource) -> None:
    columns = {unwrap(column.name): column for column in _table(sample, "gen").columns}
    assert columns["a"].hidden == 0
    assert columns["b"].hidden == 2


def test_without_rowid_tables_are_flagged(sample: db.DbSource) -> None:
    assert _table(sample, "sin_fila").without_rowid is True
    assert _table(sample, "clientes").without_rowid is False


def test_indexes_keep_uniqueness_and_columns_and_expressions_have_no_name(
    sample: db.DbSource,
) -> None:
    indexes = {unwrap(index.name): index for index in _table(sample, "clientes").indexes}
    assert indexes["idx_email"].unique is False
    assert [
        None if column is None else unwrap(column) for column in indexes["idx_email"].columns
    ] == ["email"]
    assert indexes["idx_expr"].columns == (None,)
    assert any(index.unique for index in indexes.values())


def test_the_schema_text_is_never_kept(sample: db.DbSource) -> None:
    assert not hasattr(sample.views[0], "sql")
    rendered = repr(sample)
    assert "CREATE" not in rendered
    assert "SELECT" not in rendered


def test_a_not_sqlite_image_is_refused() -> None:
    with pytest.raises(AsfbError) as info:
        db.read_bytes(b"\x89PNG" + b"\x00" * 100)
    assert info.value.code is ErrorCode.E_DB_NOT_SQLITE


def test_an_image_over_the_limit_is_refused() -> None:
    data = db.HEADER + b"\x00" * limits_module.MAX_DB_BYTES
    assert db._read_bytes(data) is ErrorCode.E_DB_TOO_LARGE


def test_a_truncated_image_is_corrupt() -> None:
    image = _image(SCHEMA)
    for size in (100, 1000, len(image) // 2):
        assert db._read_bytes(image[:size]) is ErrorCode.E_DB_CORRUPT


def test_a_damaged_table_page_fails_the_quick_check() -> None:
    script = "CREATE TABLE t(a INTEGER PRIMARY KEY, b TEXT);" + (
        "WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM r WHERE i < 500) "
        "INSERT INTO t(b) SELECT printf('%0200d', i) FROM r;"
    )
    image = bytearray(_image(script))
    assert isinstance(db._read_bytes(bytes(image)), db.DbSource)  # the intact image reads
    page = 4096  # page 2 is the table root in this image
    image[page : page + 64] = b"\xff" * 64
    assert db._read_bytes(bytes(image)) is ErrorCode.E_DB_CORRUPT


def test_a_wal_mode_header_is_refused_without_a_wal_file() -> None:
    image = bytearray(_image("CREATE TABLE t(a);"))
    image[18] = 2
    image[19] = 2
    assert db._read_bytes(bytes(image)) is ErrorCode.E_DB_CORRUPT


def test_an_unsupported_runtime_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "runtime_supported", lambda: False)
    assert db._read_bytes(_image("CREATE TABLE t(a);")) is ErrorCode.E_DB_UNSUPPORTED_RUNTIME


def test_the_runtime_check_is_python_3_11_or_later() -> None:
    assert db.runtime_supported((3, 11)) is True
    assert db.runtime_supported((3, 12, 1)) is True
    assert db.runtime_supported((3, 10)) is False


def test_a_budget_that_runs_out_makes_the_count_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    # count(*) on a plain table is one btree call, so the budget is exercised through _count with a
    # callback on every instruction and no allowance: the first callback exhausts the budget.
    monkeypatch.setattr(db, "_OPS_PER_CALL", 1)
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.executescript("CREATE TABLE big(n INTEGER); INSERT INTO big VALUES (1), (2);")
        guard = db._Guard(regular=frozenset({"big"}))
        connection.set_authorizer(db._wrap_authorizer(guard))
        connection.set_progress_handler(lambda: db._progress(guard), db._OPS_PER_CALL)
        monkeypatch.setattr(db, "_BUDGET_CALLS", 0)
        assert db._count(connection, guard, "big") is None
        assert guard.hit is True
        monkeypatch.setattr(db, "_BUDGET_CALLS", 10**9)
        assert db._count(connection, guard, "big") == 2
    finally:
        connection.close()


def test_the_budget_is_per_query_and_small_counts_stay_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db, "_BUDGET_CALLS", 1000)
    assert _table(db.read_bytes(_image(SCHEMA)), "clientes").rows == 2


def test_a_schema_over_the_table_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits_module, "MAX_DB_TABLES", 2)
    script = "".join(f"CREATE TABLE t{i}(a);" for i in range(3))
    assert db._read_bytes(_image(script)) is ErrorCode.E_DB_LIMIT_EXCEEDED


def test_a_table_over_the_column_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits_module, "MAX_DB_COLUMNS", 3)
    columns = ", ".join(f"c{i}" for i in range(4))
    assert db._read_bytes(_image(f"CREATE TABLE wide({columns});")) is ErrorCode.E_DB_LIMIT_EXCEEDED


def test_views_and_triggers_over_the_limit_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits_module, "MAX_DB_TABLES", 1)
    script = (
        "CREATE TABLE t(a); CREATE VIEW v1 AS SELECT a FROM t; CREATE VIEW v2 AS SELECT a FROM t;"
    )
    assert db._read_bytes(_image(script)) is ErrorCode.E_DB_LIMIT_EXCEEDED


def test_an_index_over_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits_module, "MAX_DB_INDEXES", 1)
    script = "CREATE TABLE t(a UNIQUE, b UNIQUE);"
    assert db._read_bytes(_image(script)) is ErrorCode.E_DB_LIMIT_EXCEEDED


def test_the_authorizer_allows_only_the_reads_the_reader_needs() -> None:
    guard = db._Guard(regular=frozenset({"clientes"}), virtual=frozenset({"busqueda"}))
    allowed = [
        (sqlite3.SQLITE_SELECT, None, None),
        (sqlite3.SQLITE_READ, "sqlite_schema", "type"),
        (sqlite3.SQLITE_READ, "sqlite_master", "sql"),
        (sqlite3.SQLITE_READ, "clientes", "email"),
        (sqlite3.SQLITE_FUNCTION, None, "count"),
        (sqlite3.SQLITE_PRAGMA, "table_xinfo", "clientes"),
        (sqlite3.SQLITE_PRAGMA, "foreign_key_list", "clientes"),
        (sqlite3.SQLITE_PRAGMA, "index_list", "clientes"),
        (sqlite3.SQLITE_PRAGMA, "index_info", "idx_email"),
    ]
    for action, arg1, arg2 in allowed:
        assert db._authorize(guard, action, arg1, arg2) == sqlite3.SQLITE_OK, (action, arg1, arg2)
    assert guard.denied is False


@pytest.mark.parametrize(
    ("action", "arg1", "arg2"),
    [
        (sqlite3.SQLITE_READ, "busqueda", "texto"),
        (sqlite3.SQLITE_READ, "sin_permiso", "a"),
        (sqlite3.SQLITE_PRAGMA, "table_xinfo", "busqueda"),
        (sqlite3.SQLITE_PRAGMA, "journal_mode", None),
        (sqlite3.SQLITE_PRAGMA, "quick_check", None),
        (sqlite3.SQLITE_FUNCTION, None, "load_extension"),
        (sqlite3.SQLITE_FUNCTION, None, "readfile"),
        (sqlite3.SQLITE_INSERT, "clientes", None),
        (sqlite3.SQLITE_UPDATE, "clientes", "email"),
        (sqlite3.SQLITE_ATTACH, "x.db", None),
        (sqlite3.SQLITE_CREATE_TABLE, "t", None),
        (sqlite3.SQLITE_TRANSACTION, "BEGIN", None),
    ],
)
def test_the_authorizer_denies_everything_else(
    action: int, arg1: str | None, arg2: str | None
) -> None:
    guard = db._Guard(regular=frozenset({"clientes"}), virtual=frozenset({"busqueda"}))
    assert db._authorize(guard, action, arg1, arg2) == sqlite3.SQLITE_DENY
    assert guard.denied is True


def test_quick_check_is_allowed_only_without_virtual_tables() -> None:
    plain = db._Guard(regular=frozenset({"t"}))
    assert db._authorize(plain, sqlite3.SQLITE_PRAGMA, "quick_check", None) == sqlite3.SQLITE_OK
    with_virtual = db._Guard(regular=frozenset({"t"}), virtual=frozenset({"v"}))
    assert db._authorize(with_virtual, sqlite3.SQLITE_PRAGMA, "quick_check", None) == (
        sqlite3.SQLITE_DENY
    )


def test_a_failed_count_name_with_a_nul_is_corrupt_not_a_message() -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        assert db._count(connection, db._Guard(), "a\x00b") is ErrorCode.E_DB_CORRUPT
    finally:
        connection.close()


def test_no_file_appears_next_to_the_database_or_in_the_temp_directory(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    path.write_bytes(_image(SCHEMA))
    before_here = sorted(os.listdir(tmp_path))
    temp = tempfile.gettempdir()
    before_temp = sorted(os.listdir(temp))
    db.read_bytes(path.read_bytes())
    assert sorted(os.listdir(tmp_path)) == before_here
    assert sorted(os.listdir(temp)) == before_temp


def test_reading_never_changes_the_bytes() -> None:
    image = _image(SCHEMA)
    snapshot = bytes(image)
    db.read_bytes(image)
    assert image == snapshot


_SURROGATES: Final[tuple[Literal["Cs"]]] = ("Cs",)
NAME = st.text(
    alphabet=st.characters(exclude_categories=_SURROGATES, exclude_characters="\x00"),
    min_size=1,
    max_size=24,
)


@settings(max_examples=40, deadline=None)
@given(name=NAME)
def test_hostile_table_names_come_back_raw(name: str) -> None:
    quoted = '"' + name.replace('"', '""') + '"'
    source = db.read_bytes(
        _image(f"CREATE TABLE {quoted}(a INTEGER); INSERT INTO {quoted} VALUES (1);")
    )
    assert [unwrap(table.name) for table in source.tables] == [name]
    assert source.tables[0].rows == 1
