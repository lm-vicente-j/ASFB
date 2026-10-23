"""Document ids of the form d_ plus 16 lowercase hex digits, derived from the session key."""

import re
from typing import Self, final

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

SCHEMA_PATTERN = r"^d_[0-9a-f]{16}$"
SHAPE_RE = re.compile(r"d_[0-9a-f]{16}")


@final
class DocId(str):
    """A document id string that has passed the format check."""

    __slots__ = ()

    def __new__(cls, value: str, /) -> Self:
        if type(value) is not str:
            raise TypeError("doc id must be an exact str")
        if SHAPE_RE.fullmatch(value) is None:
            raise ValueError("invalid doc id format")
        return super().__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            lambda value: cls(str(value)),
            core_schema.str_schema(pattern=SCHEMA_PATTERN, strict=True),
        )
