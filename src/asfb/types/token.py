"""Deterministic token strings of the form t_ plus 16 lowercase hex digits."""

import re
from typing import Self, final

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

SCHEMA_PATTERN = r"^t_[0-9a-f]{16}$"
SHAPE_RE = re.compile(r"t_[0-9a-f]{16}")


@final
class Token(str):
    """A token string that has passed the format check."""

    __slots__ = ()

    def __new__(cls, value: str, /) -> Self:
        if type(value) is not str:
            raise TypeError("token must be an exact str")
        if SHAPE_RE.fullmatch(value) is None:
            raise ValueError("invalid token format")
        return super().__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            lambda value: cls(str(value)),
            core_schema.str_schema(pattern=SCHEMA_PATTERN, strict=True),
        )
