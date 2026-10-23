"""(token, surface) pairs shared by every sanitizer. Pairs hold originals: only the vault gets them.

A surface is the decoded value with outer ASCII whitespace stripped, taken before Cf removal and
NFC, so variants that normalize to one token stay distinct surfaces. SurfacePairs cannot be
pickled, copied or printed.
"""

from typing import Final, NoReturn, SupportsIndex, final

from asfb.types.token import Token

_SURFACE_WS: Final = " \t\n\f\r"


def surface_of(raw: str) -> str:
    """The surface of a raw value: outer ASCII whitespace stripped, before Cf removal and NFC."""
    return raw.strip(_SURFACE_WS)


class PairRecorder:
    """Collects the pairs one censoring run mints, in first-seen order, without duplicates."""

    __slots__ = ("_pairs",)

    def __init__(self) -> None:
        self._pairs: dict[tuple[Token, str], None] = {}

    def record(self, token: Token, surface: str) -> None:
        self._pairs[(token, surface)] = None

    def items(self) -> tuple[tuple[Token, str], ...]:
        return tuple(self._pairs)


@final
class SurfacePairs:
    """(token, surface) pairs of one censoring run. Holds originals: redacted, not copyable."""

    __slots__ = ("_items",)

    def __init__(self, items: tuple[tuple[Token, str], ...], /) -> None:
        self._items = items

    def items(self) -> tuple[tuple[Token, str], ...]:
        return self._items

    def __repr__(self) -> str:
        return "SurfacePairs(<redacted>)"

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> NoReturn:
        raise TypeError("SurfacePairs cannot be pickled")

    def __copy__(self) -> NoReturn:
        raise TypeError("SurfacePairs cannot be copied")

    def __deepcopy__(self, memo: object = None, /) -> NoReturn:
        raise TypeError("SurfacePairs cannot be copied")
