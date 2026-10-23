"""The v1 listing model: the documents found under a directory, one entry each."""

from typing import Annotated, Literal

from pydantic import Field

from asfb.schema.doc import Profile, VocabRef
from asfb.types.base import StrictModel
from asfb.types.docid import DocId
from asfb.types.token import Token

MAX_ENTRIES = 10_000
MAX_PATH = 64
ListExt = Literal[
    "html",
    "htm",
    "txt",
    "md",
    "markdown",
    "py",
    "pyi",
    "log",
    "jsonl",
    "ndjson",
    "db",
    "sqlite",
    "sqlite3",
]


class ListEntry(StrictModel):
    """One document: its id, its path segments (tokens, file stem last) and its extension."""

    doc: DocId
    path: Annotated[tuple[Token, ...], Field(max_length=MAX_PATH)]
    ext: ListExt


class ListV1(StrictModel):
    schema_id: Literal["asfb.list/1"] = "asfb.list/1"
    profile: Profile
    vocab: Annotated[tuple[VocabRef, ...], Field(max_length=0)]
    entries: Annotated[tuple[ListEntry, ...], Field(max_length=MAX_ENTRIES)]
