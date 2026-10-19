"""The v1 document model. Only the shapes below can reach egress."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from asfb.schema.html import Node
from asfb.types.base import StrictModel
from asfb.types.values import VocabId


class Profile(StrEnum):
    STRICT = "strict"
    DIFFABLE = "diffable"


class SourceKind(StrEnum):
    HTML = "html"


class VocabRef(StrictModel):
    id: VocabId
    version: Annotated[int, Field(ge=1, le=65535)]


class DocV1(StrictModel):
    schema_id: Literal["asfb.doc/1"] = "asfb.doc/1"
    profile: Profile
    source: SourceKind
    vocab: tuple[VocabRef, ...]
    body: Annotated[tuple[Node, ...], Field(max_length=20000)]
