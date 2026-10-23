"""SQLite files: the client-side checks, and the hardened reader that runs in the worker.

The client (read_file) checks the 16-byte header and the size before it reads the bytes, then
records whether a -wal file sits next to the database. The WAL itself is never read. The reader
(read_bytes, worker only) never gives SQLite a path: the bytes are loaded into an in-memory
connection by deserialize. Before the first read the connection is hardened: query_only,
trusted_schema off, defensive mode where the runtime has it, temp files in memory, no ATTACH and
no extensions. An authorizer allows only reads of the schema, the table, index and foreign key
pragmas, quick_check on databases without virtual tables, and count(). A progress handler bounds
every query. No SQLite message is ever kept: a failure becomes a static code.

Virtual tables are never connected: connecting one runs module code, so they are listed with their
name only and an unknown row count. Public functions raise AsfbError outside any except block.
"""

import base64
import binascii
import os
import re
import sqlite3
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import limits
from asfb.types.untrusted import Untrusted

HEADER: Final = b"SQLite format 3\x00"
WAL_SUFFIX: Final = "-wal"
_SUFFIXES: Final = frozenset({".db", ".sqlite", ".sqlite3"})
_MIN_RUNTIME: Final = (3, 11)
_PAGE_MIN: Final = 512
_PAGE_MAX: Final = 65536
_OPS_PER_CALL: Final = 1000  # VM instructions between two progress callbacks
_BUDGET_CALLS: Final = 20_000  # callbacks one query may use: 20 million instructions
# quick_check walks every page inside one statement, so it gets a larger budget. The worker's CPU
# limit still ends a check that runs for too long.
_CHECK_BUDGET_CALLS: Final = 2_000_000
_QUICK_CHECK_OK: Final = [("ok",)]
_VIRTUAL: Final = re.compile(r"\s*CREATE\s+VIRTUAL\s+TABLE", re.IGNORECASE)
_TABLE_OPTIONS: Final = re.compile(r"\bWITHOUT\s+ROWID\b", re.IGNORECASE)
_SCHEMA_NAMES: Final = frozenset({"sqlite_schema", "sqlite_master"})
# Pragmas the reader may run. quick_check is allowed only while no virtual table is present.
_PRAGMAS: Final = frozenset({"table_xinfo", "foreign_key_list", "index_list", "index_info"})
_CHECK_PRAGMA: Final = "quick_check"
_OK: Final = sqlite3.SQLITE_OK
_DENY: Final = sqlite3.SQLITE_DENY
_Authorizer = Callable[[int, str | None, str | None, str | None, str | None], int]


@dataclass(frozen=True, slots=True, eq=False)
class DbFile:
    """The bytes of one database file and whether its WAL file exists (never read)."""

    data: bytes
    wal_present: bool


@dataclass(frozen=True, slots=True, eq=False)
class DbForeignKey:
    """One foreign key of a column: the target table and column (None: the target's key)."""

    table: Untrusted[str]
    column: Untrusted[str] | None


@dataclass(frozen=True, slots=True, eq=False)
class DbColumn:
    """One column. declared is None when the column has no declared type."""

    name: Untrusted[str]
    declared: Untrusted[str] | None
    notnull: bool
    pk: int
    has_default: bool
    hidden: int  # 0 normal, 1 hidden, 2 generated virtual, 3 generated stored
    foreign_keys: tuple[DbForeignKey, ...]


@dataclass(frozen=True, slots=True, eq=False)
class DbIndex:
    """One index. A None column is an expression, which has no name."""

    name: Untrusted[str]
    unique: bool
    columns: tuple[Untrusted[str] | None, ...]


@dataclass(frozen=True, slots=True, eq=False)
class DbTable:
    """One table. rows is None when the count is unknown (virtual table or budget exhausted)."""

    name: Untrusted[str]
    without_rowid: bool
    rows: int | None
    columns: tuple[DbColumn, ...]
    indexes: tuple[DbIndex, ...]


@dataclass(frozen=True, slots=True, eq=False)
class DbTrigger:
    name: Untrusted[str]
    table: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class DbSource:
    """A parsed database: its page size, its tables, its view names and its triggers."""

    page_size: int
    tables: tuple[DbTable, ...]
    views: tuple[Untrusted[str], ...]
    triggers: tuple[DbTrigger, ...]


@dataclass(slots=True)
class _Guard:
    """Authorizer and budget state of one reader. regular and virtual are set from the schema."""

    regular: frozenset[str] = frozenset()
    virtual: frozenset[str] = frozenset()
    denied: bool = False
    limit: int = _BUDGET_CALLS
    calls: int = 0
    hit: bool = False

    def reset(self) -> None:
        self.calls = 0
        self.hit = False


def is_database_name(name: str, /) -> bool:
    """True for the suffixes SQLite files use (.db, .sqlite, .sqlite3), in any case."""
    return os.path.splitext(name)[1].lower() in _SUFFIXES


def header_ok(data: bytes, /) -> bool:
    return type(data) is bytes and data[: len(HEADER)] == HEADER


def runtime_supported(version: Sequence[int] = sys.version_info[:2], /) -> bool:
    """deserialize, setlimit and the connection config calls need Python 3.11 or later."""
    return tuple(version[:2]) >= _MIN_RUNTIME


def _read(path: str) -> DbFile | ErrorCode:
    try:
        with open(path, "rb") as handle:
            if handle.read(len(HEADER)) != HEADER:
                return ErrorCode.E_DB_NOT_SQLITE
            if os.fstat(handle.fileno()).st_size > limits.MAX_DB_BYTES:
                return ErrorCode.E_DB_TOO_LARGE
            handle.seek(0)
            data = handle.read(limits.MAX_DB_BYTES + 1)
    except (OSError, ValueError):
        return ErrorCode.E_SOURCE_READ
    if len(data) > limits.MAX_DB_BYTES:
        return ErrorCode.E_DB_TOO_LARGE
    if not header_ok(data):
        return ErrorCode.E_DB_NOT_SQLITE
    return DbFile(data=data, wal_present=os.path.lexists(path + WAL_SUFFIX))


def read_file(path: str, /) -> DbFile | ErrorCode:
    """The bytes of a SQLite file with the header checked first. Never raises."""
    if type(path) is not str:
        return ErrorCode.E_SOURCE_UNSUPPORTED
    return _read(path)


def check_file(path: str, /) -> DbFile:
    """read_file for a caller that raises. Raises AsfbError with a static code."""
    outcome = read_file(path)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def encode_bytes(data: bytes, /) -> str:
    """The base64 text the worker request carries for a database file."""
    return base64.b64encode(data).decode("ascii")


def decode_bytes(text: str, /) -> bytes | ErrorCode:
    if type(text) is not str:
        return ErrorCode.E_WORKER_PROTOCOL
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return ErrorCode.E_WORKER_PROTOCOL


def _quote(name: str) -> str:
    """A name as one SQL identifier: double quotes, with embedded quotes doubled."""
    return '"' + name.replace('"', '""') + '"'


def _page_size(data: bytes) -> int | ErrorCode:
    raw = int.from_bytes(data[16:18], "big")
    size = 65536 if raw == 1 else raw
    if not (_PAGE_MIN <= size <= _PAGE_MAX and size & (size - 1) == 0):
        return ErrorCode.E_DB_CORRUPT
    return size


def _authorize(guard: _Guard, action: int, arg1: str | None, arg2: str | None) -> int:
    """Allow only the reads and pragmas the reader needs. Every other action is denied."""
    allowed = False
    if action == sqlite3.SQLITE_SELECT:
        allowed = True
    elif action == sqlite3.SQLITE_READ:
        allowed = arg1 in _SCHEMA_NAMES or arg1 in guard.regular
    elif action == sqlite3.SQLITE_FUNCTION:
        allowed = arg2 == "count"
    elif action == sqlite3.SQLITE_PRAGMA:
        if arg1 == _CHECK_PRAGMA:
            allowed = not guard.virtual
        elif arg1 in _PRAGMAS:
            allowed = arg2 is None or arg2 not in guard.virtual
    if not allowed:
        guard.denied = True
        return _DENY
    return _OK


def _wrap_authorizer(guard: _Guard) -> _Authorizer:
    """The authorizer SQLite calls: SQLite passes five arguments, only three matter here."""

    def authorize(
        action: int, arg1: str | None, arg2: str | None, database: str | None, source: str | None
    ) -> int:
        del database, source
        return _authorize(guard, action, arg1, arg2)

    return authorize


def _progress(guard: _Guard) -> int:
    guard.calls += 1
    if guard.calls > guard.limit:
        guard.hit = True
        return 1
    return 0


def _harden(connection: sqlite3.Connection) -> None:
    """The settings applied before the database is loaded. Missing optional calls are skipped."""
    # trusted_schema is a pragma so that it holds on every runtime; setconfig is 3.12 and later.
    if hasattr(connection, "setconfig"):
        connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
    if hasattr(connection, "enable_load_extension"):
        connection.enable_load_extension(False)
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA temp_store = MEMORY")


def _failure(guard: _Guard) -> ErrorCode:
    """The static code of a failed statement, from the guard state only (never the message)."""
    if guard.denied:
        return ErrorCode.E_DB_DENIED
    if guard.hit:
        return ErrorCode.E_DB_BUDGET_EXCEEDED
    return ErrorCode.E_DB_CORRUPT


def _rows(
    connection: sqlite3.Connection, guard: _Guard, sql: str, limit: int | None = None
) -> list[tuple[object, ...]] | ErrorCode:
    guard.reset()
    # Read at call time, so the budget is the module value now and not the value at definition.
    guard.limit = _BUDGET_CALLS if limit is None else limit
    try:
        rows: list[tuple[object, ...]] = connection.execute(sql).fetchall()
    except (sqlite3.Error, ValueError, OverflowError):
        return _failure(guard)
    return rows


def _seqno(row: tuple[object, ...]) -> int:
    value = row[0]
    return value if type(value) is int else 0


def _count(connection: sqlite3.Connection, guard: _Guard, name: str) -> int | ErrorCode | None:
    """The row count, or None when the budget ran out. Any other failure is a code."""
    # The name is one quoted identifier from _quote; no value is interpolated.
    rows = _rows(connection, guard, f"SELECT count(*) FROM {_quote(name)}")  # noqa: S608
    if isinstance(rows, ErrorCode):
        return None if guard.hit else rows
    value = rows[0][0] if rows else None
    return value if type(value) is int else ErrorCode.E_DB_CORRUPT


def _schema_rows(
    connection: sqlite3.Connection, guard: _Guard
) -> list[tuple[str, str, str, str | None]] | ErrorCode:
    rows = _rows(connection, guard, "SELECT type, name, tbl_name, sql FROM sqlite_schema")
    if isinstance(rows, ErrorCode):
        return rows
    schema: list[tuple[str, str, str, str | None]] = []
    for row in rows:
        kind, name, owner, sql = row
        if not (isinstance(kind, str) and isinstance(name, str) and isinstance(owner, str)):
            return ErrorCode.E_DB_CORRUPT
        schema.append((kind, name, owner, sql if isinstance(sql, str) else None))
    return schema


def _columns(
    connection: sqlite3.Connection, guard: _Guard, table: str
) -> tuple[DbColumn, ...] | ErrorCode:
    rows = _rows(connection, guard, f"PRAGMA table_xinfo({_quote(table)})")
    if isinstance(rows, ErrorCode):
        return rows
    if len(rows) > limits.MAX_DB_COLUMNS:
        return ErrorCode.E_DB_LIMIT_EXCEEDED
    foreign = _foreign_keys(connection, guard, table)
    if isinstance(foreign, ErrorCode):
        return foreign
    columns: list[DbColumn] = []
    for row in rows:
        _cid, name, declared, notnull, default, pk, hidden = row
        if not isinstance(name, str) or type(hidden) is not int or type(pk) is not int:
            return ErrorCode.E_DB_CORRUPT
        kind = declared if isinstance(declared, str) and declared != "" else None
        columns.append(
            DbColumn(
                name=Untrusted(name),
                declared=None if kind is None else Untrusted(kind),
                notnull=bool(notnull),
                pk=pk,
                has_default=default is not None,
                hidden=hidden,
                foreign_keys=foreign.get(name, ()),
            )
        )
    return tuple(columns)


def _foreign_keys(
    connection: sqlite3.Connection, guard: _Guard, table: str
) -> dict[str, tuple[DbForeignKey, ...]] | ErrorCode:
    rows = _rows(connection, guard, f"PRAGMA foreign_key_list({_quote(table)})")
    if isinstance(rows, ErrorCode):
        return rows
    found: dict[str, list[DbForeignKey]] = {}
    for row in rows:
        target, source, column = row[2], row[3], row[4]
        if not isinstance(target, str) or not isinstance(source, str):
            return ErrorCode.E_DB_CORRUPT
        link = DbForeignKey(Untrusted(target), None if column is None else Untrusted(str(column)))
        found.setdefault(source, []).append(link)
    return {name: tuple(links) for name, links in found.items()}


def _indexes(
    connection: sqlite3.Connection, guard: _Guard, table: str
) -> tuple[DbIndex, ...] | ErrorCode:
    rows = _rows(connection, guard, f"PRAGMA index_list({_quote(table)})")
    if isinstance(rows, ErrorCode):
        return rows
    indexes: list[DbIndex] = []
    for row in rows:
        name, unique = row[1], row[2]
        if not isinstance(name, str) or type(unique) is not int:
            return ErrorCode.E_DB_CORRUPT
        parts = _rows(connection, guard, f"PRAGMA index_info({_quote(name)})")
        if isinstance(parts, ErrorCode):
            return parts
        columns = tuple(
            None if part[2] is None else Untrusted(str(part[2]))
            for part in sorted(parts, key=_seqno)
        )
        indexes.append(DbIndex(name=Untrusted(name), unique=bool(unique), columns=columns))
    return tuple(indexes)


def _table(
    connection: sqlite3.Connection, guard: _Guard, name: str, sql: str | None
) -> DbTable | ErrorCode:
    virtual = sql is not None and _VIRTUAL.match(sql) is not None
    without_rowid = not virtual and sql is not None and _without_rowid(sql)
    if virtual:
        return DbTable(Untrusted(name), False, None, (), ())
    count = _count(connection, guard, name)
    if isinstance(count, ErrorCode):
        return count
    columns = _columns(connection, guard, name)
    if isinstance(columns, ErrorCode):
        return columns
    indexes = _indexes(connection, guard, name)
    if isinstance(indexes, ErrorCode):
        return indexes
    return DbTable(Untrusted(name), without_rowid, count, columns, indexes)


def _without_rowid(sql: str) -> bool:
    """Table options follow the last closing parenthesis of the definition."""
    tail = sql[sql.rfind(")") + 1 :] if ")" in sql else ""
    return _TABLE_OPTIONS.search(tail) is not None


def _load(connection: sqlite3.Connection, data: bytes) -> ErrorCode | None:
    try:
        connection.deserialize(data)
    except (sqlite3.Error, ValueError, OverflowError, MemoryError):
        return ErrorCode.E_DB_CORRUPT
    return None


def _inspect(data: bytes) -> DbSource | ErrorCode:
    if not runtime_supported():
        return ErrorCode.E_DB_UNSUPPORTED_RUNTIME
    if len(data) > limits.MAX_DB_BYTES:
        return ErrorCode.E_DB_TOO_LARGE
    if not header_ok(data):
        return ErrorCode.E_DB_NOT_SQLITE
    page = _page_size(data)
    if isinstance(page, ErrorCode):
        return page
    guard = _Guard()
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        _harden(connection)
        # Loading runs SQLite's own internal statements, which the authorizer would refuse, so the
        # image is loaded first (query_only is already on) and the guard goes on before any read.
        loaded = _load(connection, data)
        if loaded is not None:
            return loaded
        connection.set_authorizer(_wrap_authorizer(guard))
        connection.set_progress_handler(lambda: _progress(guard), _OPS_PER_CALL)
        return _read_schema(connection, guard, page)
    finally:
        connection.close()


def _read_schema(connection: sqlite3.Connection, guard: _Guard, page: int) -> DbSource | ErrorCode:
    schema = _schema_rows(connection, guard)
    if isinstance(schema, ErrorCode):
        return schema
    names = [(name, sql) for kind, name, _owner, sql in schema if kind == "table"]
    if len(names) > limits.MAX_DB_TABLES:
        return ErrorCode.E_DB_LIMIT_EXCEEDED
    virtual = frozenset(name for name, sql in names if sql and _VIRTUAL.match(sql))
    guard.virtual = virtual
    guard.regular = frozenset(name for name, _sql in names) - virtual
    if not virtual:
        check = _rows(connection, guard, f"PRAGMA {_CHECK_PRAGMA}(1)", _CHECK_BUDGET_CALLS)
        if isinstance(check, ErrorCode):
            return check
        if check != _QUICK_CHECK_OK:
            return ErrorCode.E_DB_CORRUPT
    tables: list[DbTable] = []
    indexes = 0
    for name, sql in names:
        table = _table(connection, guard, name, sql)
        if isinstance(table, ErrorCode):
            return table
        indexes += len(table.indexes)
        tables.append(table)
    if indexes > limits.MAX_DB_INDEXES:
        return ErrorCode.E_DB_LIMIT_EXCEEDED
    views = tuple(Untrusted(name) for kind, name, _owner, _sql in schema if kind == "view")
    triggers = tuple(
        DbTrigger(Untrusted(name), Untrusted(owner))
        for kind, name, owner, _sql in schema
        if kind == "trigger"
    )
    # The model bounds views and triggers like tables; an excess is refused here, not at egress.
    if len(views) > limits.MAX_DB_TABLES or len(triggers) > limits.MAX_DB_TABLES:
        return ErrorCode.E_DB_LIMIT_EXCEEDED
    return DbSource(page_size=page, tables=tuple(tables), views=views, triggers=triggers)


def _read_bytes(data: bytes) -> DbSource | ErrorCode:
    """The reader with every failure as a code. Runs only in the worker, under the db job audit."""
    try:
        return _inspect(data)
    except (sqlite3.Error, ValueError, OverflowError, TypeError, UnicodeError):
        return ErrorCode.E_DB_CORRUPT


def read_bytes(data: bytes, /) -> DbSource:
    """Parse a SQLite database image without giving SQLite a path. Raises AsfbError."""
    outcome = _read_bytes(data)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome
