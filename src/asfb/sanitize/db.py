"""Database censoring: header, tables, columns, indexes, views and triggers, into the v1 model.

Rules, applied to the reader's DbSource:
- A name is a token of its raw surface. A table, index, view or trigger name is public only when it
  starts with sqlite_ and is a db.sqlite term; a column name is always a token.
- A declared type is its base name (text before the first parenthesis, ASCII whitespace stripped).
  The base name is public only when it is a db.sqltypes term. No declared type is None.
- The affinity is computed with SQLite's five rules from the full declared text. It is public.
- A row count is a log2 bucket; an unknown count is "unknown". The page size is a log2 bucket.
- Defaults and checks are only their presence. No value, no SQL text is ever minted or emitted.

Every token minted here records its (token, surface) pair for the vault. The WAL flag is not known
to the worker: the caller sets it with with_wal() before the model is emitted.
"""

from typing import Final

from asfb.ingest import db as ingest
from asfb.sanitize.pairs import PairRecorder, SurfacePairs, surface_of
from asfb.sanitize.tokens import token_for
from asfb.schema import db as schema
from asfb.schema.doc import Profile, VocabRef
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted, unwrap
from asfb.types.values import Bucket, EnumValue
from asfb.vocab import DB_IDS, registry

_INTERNAL_PREFIX: Final = "sqlite_"
_HIDDEN: Final[dict[int, schema.Hidden]] = {
    0: "normal",
    1: "hidden",
    2: "generated_virtual",
    3: "generated_stored",
}


def _mint(key: SessionKey, recorder: PairRecorder, raw: str) -> Token:
    """The token of a raw name, and its (token, surface) pair for the vault."""
    token = token_for(key, Untrusted(raw))
    recorder.record(token, surface_of(raw))
    return token


def _name(key: SessionKey, recorder: PairRecorder, raw: str) -> schema.Name:
    """A table, index, view or trigger name: an internal sqlite_ name is public, others tokens."""
    if raw.startswith(_INTERNAL_PREFIX) and raw in registry()["db.sqlite"]:
        return EnumValue(vocab="db.sqlite", term=raw)
    return _mint(key, recorder, raw)


def affinity_of(declared: str | None, /) -> str:
    """SQLite's affinity rules, in their order, over the declared type text (ASCII case only)."""
    if declared is None:
        return "BLOB"
    upper = "".join(char.upper() if char.isascii() else char for char in declared)
    if "INT" in upper:
        return "INTEGER"
    if any(part in upper for part in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in upper or upper == "":
        return "BLOB"
    if any(part in upper for part in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _declared(key: SessionKey, recorder: PairRecorder, raw: str | None) -> schema.Name | None:
    if raw is None:
        return None
    base = raw.split("(", 1)[0].strip(" \t\n\f\r")
    if base in registry()["db.sqltypes"]:
        return EnumValue(vocab="db.sqltypes", term=base)
    return _mint(key, recorder, base)


def _column(key: SessionKey, recorder: PairRecorder, column: ingest.DbColumn) -> schema.DbColumn:
    declared = None if column.declared is None else unwrap(column.declared)
    links = tuple(
        schema.DbForeignKey(
            table=_name(key, recorder, unwrap(link.table)),
            column=None if link.column is None else _mint(key, recorder, unwrap(link.column)),
        )
        for link in column.foreign_keys
    )
    return schema.DbColumn(
        name=_mint(key, recorder, unwrap(column.name)),
        declared=_declared(key, recorder, declared),
        affinity=EnumValue(vocab="db.sqlite", term=affinity_of(declared)),
        notnull=column.notnull,
        pk=column.pk,
        default=column.has_default,
        hidden=_HIDDEN[column.hidden],
        foreign_keys=links,
    )


def _index(key: SessionKey, recorder: PairRecorder, index: ingest.DbIndex) -> schema.DbIndex:
    columns = tuple(
        None if column is None else _mint(key, recorder, unwrap(column)) for column in index.columns
    )
    return schema.DbIndex(
        name=_name(key, recorder, unwrap(index.name)),
        unique=index.unique,
        columns=columns,
    )


def _table(key: SessionKey, recorder: PairRecorder, table: ingest.DbTable) -> schema.DbTable:
    rows: schema.Rows = "unknown" if table.rows is None else Bucket.log2(table.rows)
    return schema.DbTable(
        name=_name(key, recorder, unwrap(table.name)),
        without_rowid=table.without_rowid,
        rows=rows,
        columns=tuple(_column(key, recorder, column) for column in table.columns),
        indexes=tuple(_index(key, recorder, index) for index in table.indexes),
    )


def censor_db_with_pairs(
    key: SessionKey, source: ingest.DbSource, profile: Profile, /
) -> tuple[schema.DbV1, SurfacePairs]:
    """Censor a parsed database into the v1 model and the (token, surface) pairs it minted."""
    recorder = PairRecorder()
    tables = tuple(_table(key, recorder, table) for table in source.tables)
    views = tuple(_name(key, recorder, unwrap(view)) for view in source.views)
    triggers = tuple(
        schema.DbTrigger(
            name=_name(key, recorder, unwrap(trigger.name)),
            table=_name(key, recorder, unwrap(trigger.table)),
        )
        for trigger in source.triggers
    )
    refs = tuple(
        VocabRef(id=registry()[vocab_id].id, version=registry()[vocab_id].version)
        for vocab_id in DB_IDS
    )
    model = schema.DbV1(
        profile=profile,
        vocab=refs,
        wal_present=False,
        page_size=Bucket.log2(source.page_size),
        tables=tables,
        views=views,
        triggers=triggers,
    )
    return model, SurfacePairs(recorder.items())


def with_wal(model: schema.DbV1, present: bool, /) -> schema.DbV1:
    """The model with the WAL flag the caller saw next to the file (the worker cannot see it)."""
    return model.model_copy(update={"wal_present": present})
