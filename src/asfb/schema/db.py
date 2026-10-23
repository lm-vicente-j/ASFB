"""The v1 database model: a header, the tables with their columns and indexes, the views and the
triggers. Nothing about the content is here: no value, no SQL text, no default expression.

Public: the internal sqlite_ names of db.sqlite, the base name of a declared type that is a
db.sqltypes term, the five affinities (db.sqlite), and the fixed words of the model. Every other
name is a token of its raw surface. A row count is a log2 bucket, or unknown (a virtual table, or
a count the budget did not finish). A page size is a log2 bucket.
"""

from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from asfb.schema.doc import Profile, VocabRef
from asfb.types.base import StrictModel
from asfb.types.token import Token
from asfb.types.values import Bucket, EnumValue

MAX_TABLES = 1000  # the ingest table limit (ingest/limits.py MAX_DB_TABLES)
MAX_COLUMNS = 2000  # the ingest column limit per table (MAX_DB_COLUMNS)
MAX_INDEXES = 5000  # the ingest index limit per database (MAX_DB_INDEXES)
DB_VOCAB_IDS: Final = ("db.sqlite", "db.sqltypes")
AFFINITIES: Final = frozenset({"TEXT", "NUMERIC", "INTEGER", "REAL", "BLOB"})
Hidden = Literal["normal", "hidden", "generated_virtual", "generated_stored"]
Rows = Bucket | Literal["unknown"]
Name = EnumValue | Token


class DbForeignKey(StrictModel):
    """A foreign key of a column: the target table name and column (None: the target's key)."""

    table: Name
    column: Token | None


class DbColumn(StrictModel):
    name: Token
    declared: EnumValue | Token | None
    affinity: EnumValue
    notnull: bool
    pk: Annotated[int, Field(ge=0, le=MAX_COLUMNS)]
    default: bool
    hidden: Hidden
    foreign_keys: Annotated[tuple[DbForeignKey, ...], Field(max_length=MAX_COLUMNS)]

    @model_validator(mode="after")
    def _vocab(self) -> Self:
        if self.affinity.vocab != "db.sqlite" or self.affinity.term not in AFFINITIES:
            raise ValueError("an affinity is one of the five db.sqlite affinities")
        if isinstance(self.declared, EnumValue) and self.declared.vocab != "db.sqltypes":
            raise ValueError("a declared type is a db.sqltypes term or a token")
        return self


class DbIndex(StrictModel):
    name: Name
    unique: bool
    columns: Annotated[tuple[Token | None, ...], Field(max_length=MAX_COLUMNS)]


class DbTable(StrictModel):
    name: Name
    without_rowid: bool
    rows: Rows
    columns: Annotated[tuple[DbColumn, ...], Field(max_length=MAX_COLUMNS)]
    indexes: Annotated[tuple[DbIndex, ...], Field(max_length=MAX_INDEXES)]


class DbTrigger(StrictModel):
    name: Name
    table: Name


class DbV1(StrictModel):
    schema_id: Literal["asfb.db/1"] = "asfb.db/1"
    profile: Profile
    vocab: Annotated[tuple[VocabRef, ...], Field(max_length=len(DB_VOCAB_IDS))]
    wal_present: bool
    page_size: Bucket
    tables: Annotated[tuple[DbTable, ...], Field(max_length=MAX_TABLES)]
    views: Annotated[tuple[Name, ...], Field(max_length=MAX_TABLES)]
    triggers: Annotated[tuple[DbTrigger, ...], Field(max_length=MAX_TABLES)]

    @model_validator(mode="after")
    def _vocab_ids(self) -> Self:
        if tuple(ref.id for ref in self.vocab) != DB_VOCAB_IDS:
            raise ValueError("db declares exactly the two db vocabularies")
        return self
