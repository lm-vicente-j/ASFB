"""Error types. Messages carry static codes only, never input text."""

from enum import StrEnum
from typing import Self


class ErrorCode(StrEnum):
    E_UNTRUSTED_ACCESS = "E_UNTRUSTED_ACCESS"
    E_TOKEN_ENCODING = "E_TOKEN_ENCODING"  # noqa: S105
    E_EGRESS_TYPE = "E_EGRESS_TYPE"
    E_EGRESS_SERIALIZE = "E_EGRESS_SERIALIZE"
    E_EGRESS_SCHEMA = "E_EGRESS_SCHEMA"
    E_EGRESS_JSONSCHEMA = "E_EGRESS_JSONSCHEMA"
    E_EGRESS_VOCAB = "E_EGRESS_VOCAB"
    E_EGRESS_STRING = "E_EGRESS_STRING"
    E_EGRESS_ROUNDTRIP = "E_EGRESS_ROUNDTRIP"


class AsfbError(Exception):
    """Base error that carries only a static code."""

    code: ErrorCode

    def __init__(self, code: ErrorCode, /) -> None:
        if type(code) is not ErrorCode:
            raise TypeError("AsfbError requires an ErrorCode")
        self.code = code
        super().__init__(code.value)

    def __str__(self) -> str:
        return self.code.value

    def __reduce__(self) -> tuple[type[Self], tuple[ErrorCode]]:
        return (type(self), (self.code,))


class EgressError(AsfbError):
    """Raised when egress refuses a document."""


class UntrustedAccessError(AsfbError, TypeError):
    """Raised when Untrusted content is used in a way that could expose it."""
