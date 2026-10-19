"""Wrapper for content that must not reach output unchecked."""

from typing import Any, NoReturn, Self, SupportsIndex, final

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema

from asfb.errors import ErrorCode, UntrustedAccessError

_REPR = "Untrusted(<redacted>)"


def _deny() -> NoReturn:
    raise UntrustedAccessError(ErrorCode.E_UNTRUSTED_ACCESS)


@final
class Untrusted[T]:
    """Holds a value that can only leave through unwrap, which only asfb.sanitize may call."""

    __slots__ = ("_value",)
    _value: T

    def __init__(self, value: T, /) -> None:
        if hasattr(self, "_value"):
            _deny()
        object.__setattr__(self, "_value", value)

    def __init_subclass__(cls, **kwargs: object) -> None:
        _deny()

    def __setattr__(self, name: str, value: object, /) -> None:
        _deny()

    def __delattr__(self, name: str, /) -> None:
        _deny()

    def __repr__(self) -> str:
        return _REPR

    def __str__(self) -> str:
        _deny()

    def __format__(self, format_spec: str, /) -> str:
        _deny()

    def __bool__(self) -> bool:
        _deny()

    def __eq__(self, other: object, /) -> bool:
        _deny()

    def __hash__(self) -> int:
        _deny()

    def __reduce__(self) -> str | tuple[Any, ...]:
        _deny()

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> str | tuple[Any, ...]:
        _deny()

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, Any] | None = None, /) -> Self:
        return self

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        _deny()


def unwrap[T](value: Untrusted[T], /) -> T:
    return value._value  # noqa: SLF001
