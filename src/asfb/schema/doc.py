"""The v1 document model. Only the shapes below can reach egress."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from asfb.types.base import StrictModel
from asfb.types.token import Token
from asfb.types.values import Bucket, EnumValue, VocabId

BoundedInt = Annotated[int, Field(ge=0, le=2**31 - 1)]
Leaf = Token | EnumValue | Bucket | bool | BoundedInt


class Profile(StrEnum):
    STRICT = "strict"
    DIFFABLE = "diffable"


class VocabRef(StrictModel):
    id: VocabId
    version: Annotated[int, Field(ge=1, le=65535)]


class DocV1(StrictModel):
    schema_id: Literal["asfb.doc/1"] = "asfb.doc/1"
    profile: Profile
    vocab: tuple[VocabRef, ...]
    items: tuple[Leaf, ...]
