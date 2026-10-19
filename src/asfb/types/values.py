"""Closed value shapes: log2 buckets and vocabulary terms."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StringConstraints

from asfb.types.base import StrictModel

VocabId = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9]*(\.[a-z0-9]+)*$", max_length=64)
]


class BucketScheme(StrEnum):
    LOG2 = "log2"


class Bucket(StrictModel):
    scheme: BucketScheme
    index: int = Field(ge=0, le=64)

    @classmethod
    def log2(cls, n: int, /) -> Self:
        if n < 0:
            raise ValueError("bucket input must be non-negative")
        return cls(scheme=BucketScheme.LOG2, index=n.bit_length())


class EnumValue(StrictModel):
    vocab: VocabId
    term: Annotated[str, StringConstraints(pattern=r"^[!-~]{1,64}$")]
