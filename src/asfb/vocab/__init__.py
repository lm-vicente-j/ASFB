"""Vocabulary protocol. Concrete vocabularies arrive in later milestones."""

from collections.abc import Mapping
from typing import Protocol


class Vocabulary(Protocol):
    """A versioned set of terms that egress may emit."""

    @property
    def id(self) -> str: ...

    @property
    def version(self) -> int: ...

    def __contains__(self, term: object, /) -> bool: ...


VocabRegistry = Mapping[str, Vocabulary]
