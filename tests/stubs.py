"""Shared test doubles."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StubVocabulary:
    id: str
    version: int
    terms: frozenset[str]

    def __contains__(self, term: object, /) -> bool:
        return term in self.terms


STUB_VOCAB = StubVocabulary(
    "test.stub", 1, frozenset({"h1", "form", "btn", "btn-primary", "fetch"})
)
