"""The v1 diff model: two stored revisions and the line ranges that differ between their views."""

from typing import Annotated, Literal

from pydantic import Field

from asfb.schema.doc import Profile, VocabRef
from asfb.types.base import StrictModel
from asfb.types.docid import DocId

MAX_HUNKS = 10_000


class RefV1(StrictModel):
    doc: DocId
    revision: Annotated[int, Field(ge=1, le=999_999)]


class Hunk(StrictModel):
    """One differing range: a_start..a_end of the first view, b_start..b_end of the second."""

    op: Literal["insert", "delete", "replace"]
    a_start: Annotated[int, Field(ge=0, le=1_000_000)]
    a_end: Annotated[int, Field(ge=0, le=1_000_000)]
    b_start: Annotated[int, Field(ge=0, le=1_000_000)]
    b_end: Annotated[int, Field(ge=0, le=1_000_000)]


class DiffV1(StrictModel):
    """Hunks index the lines of two censored views, the lexemes of two Python sources or the
    records of two logs."""

    schema_id: Literal["asfb.diff/1"] = "asfb.diff/1"
    profile: Profile
    vocab: Annotated[tuple[VocabRef, ...], Field(max_length=0)]
    units: Literal["line", "lexeme", "record", "schema"] = "line"
    a: RefV1
    b: RefV1
    hunks: Annotated[tuple[Hunk, ...], Field(max_length=MAX_HUNKS)]
