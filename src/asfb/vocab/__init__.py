"""Vocabularies that egress may emit.

Vocabulary protocol and registry type are unchanged. Concrete vocabularies are JSON data
in vocab/data/<id>.json, loaded and format-checked here. Failures raise E_VOCAB_DATA.
"""

import hashlib
import itertools
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from types import MappingProxyType
from typing import Final, Protocol, final

from asfb.errors import AsfbError, ErrorCode


class Vocabulary(Protocol):
    """A versioned set of terms that egress may emit."""

    @property
    def id(self) -> str: ...

    @property
    def version(self) -> int: ...

    def __contains__(self, term: object, /) -> bool: ...


VocabRegistry = Mapping[str, Vocabulary]

HTML_IDS: Final = (
    "css.bootstrap",
    "html.attributes",
    "html.elements",
    "html.values",
    "url.schemes",
)
RUN_IDS: Final = ("py.exceptions", "run.tools")
MD_IDS: Final = ("md.languages", "url.schemes")
PY_IDS: Final = ("py.builtins", "py.exceptions", "py.keywords", "py.stdlib")
LOG_IDS: Final = ("log.fields", "log.levels")
DB_IDS: Final = ("db.sqlite", "db.sqltypes")
# Every vocabulary egress may emit, each id once.
ALL_IDS: Final = tuple(dict.fromkeys((*HTML_IDS, *RUN_IDS, *MD_IDS, *PY_IDS, *LOG_IDS, *DB_IDS)))
# WHATWG void elements: never pushed on the open stack, rendered without an end tag.
VOID_ELEMENTS: Final = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
_DATA_DIR: Traversable = resources.files("asfb.vocab") / "data"
_ID_RE: Final = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z0-9]+)*")
_TERM_RE: Final = re.compile(r"[A-Za-z0-9_.:/+-]{1,64}")
# Token and document id shapes: a term of this shape would be rewritten by the sandbox client.
_REWRITABLE_RE: Final = re.compile(r"[td]_[0-9a-f]{16}")
_MAX_ID_LENGTH: Final = 64
_MAX_VERSION: Final = 65535
_KEYS: Final = frozenset({"id", "version", "terms", "by_attribute", "source", "sha256"})
_SOURCE_KEYS: Final = frozenset({"tool", "version"})
_SOURCE_RE: Final = re.compile(r"[A-Za-z0-9._+-]{1,32}")
_DATA_ERROR: Final = ErrorCode.E_VOCAB_DATA


@final
@dataclass(frozen=True, slots=True)
class FrozenVocabulary:
    """Immutable vocabulary. by_attribute maps "*" or an element name to attribute values."""

    id: str
    version: int
    terms: frozenset[str]
    by_attribute: Mapping[str, Mapping[str, frozenset[str]]]

    def __contains__(self, term: object, /) -> bool:
        return term in self.terms

    def values_for(self, element: str, attribute: str, /) -> frozenset[str] | None:
        """The element's entry for the attribute wins over "*"; None if neither has one."""
        for scope in (element, "*"):
            entry = self.by_attribute.get(scope)
            if entry is not None and attribute in entry:
                return entry[attribute]
        return None


def _terms(raw: object) -> tuple[str, ...] | None:
    if not isinstance(raw, list) or not all(isinstance(term, str) for term in raw):
        return None
    if not all(_TERM_RE.fullmatch(term) for term in raw):
        return None
    if any(_REWRITABLE_RE.fullmatch(term) for term in raw):
        return None
    if any(before >= after for before, after in itertools.pairwise(raw)):
        return None
    return tuple(raw)


def _by_attribute(
    raw: object, terms: frozenset[str]
) -> Mapping[str, Mapping[str, frozenset[str]]] | None:
    if not isinstance(raw, dict):
        return None
    result: dict[str, Mapping[str, frozenset[str]]] = {}
    for scope, attributes in raw.items():
        if not isinstance(scope, str) or not isinstance(attributes, dict):
            return None
        table: dict[str, frozenset[str]] = {}
        for attribute, values in attributes.items():
            items = _terms(values)
            if not isinstance(attribute, str) or items is None or not set(items) <= terms:
                return None
            table[attribute] = frozenset(items)
        result[scope] = MappingProxyType(table)
    return MappingProxyType(result)


def _source_ok(raw: object) -> bool:
    """Optional metadata: the tool and version a vocabulary was derived from. Never emitted."""
    if raw is None:
        return True
    if not isinstance(raw, dict) or set(raw) != _SOURCE_KEYS:
        return False
    return all(
        type(value) is str and _SOURCE_RE.fullmatch(value) is not None for value in raw.values()
    )


def _digest(terms: tuple[str, ...]) -> str:
    """The sha256 of the canonical JSON of the terms (ASCII, no spaces); tools/gen_py_vocab.py."""
    canonical = json.dumps(list(terms), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _build(vocab_id: str, text: str) -> FrozenVocabulary | ErrorCode:
    try:
        raw: object = json.loads(text)
    except (ValueError, RecursionError):
        return _DATA_ERROR
    if not isinstance(raw, dict) or not set(raw) <= _KEYS:
        return _DATA_ERROR
    version = raw.get("version")
    if type(version) is not int or not 1 <= version <= _MAX_VERSION:
        return _DATA_ERROR
    if raw.get("id") != vocab_id or not _source_ok(raw.get("source")):
        return _DATA_ERROR
    terms = _terms(raw.get("terms"))
    if terms is None:
        return _DATA_ERROR
    if "sha256" in raw and raw["sha256"] != _digest(terms):
        return _DATA_ERROR
    by_attribute = _by_attribute(raw.get("by_attribute", {}), frozenset(terms))
    if by_attribute is None:
        return _DATA_ERROR
    return FrozenVocabulary(
        id=vocab_id,
        version=version,
        terms=frozenset(terms),
        by_attribute=by_attribute,
    )


def _read(vocab_id: str) -> str | None:
    try:
        return (_DATA_DIR / f"{vocab_id}.json").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _load(vocab_id: str) -> FrozenVocabulary | ErrorCode:
    if (
        type(vocab_id) is not str
        or len(vocab_id) > _MAX_ID_LENGTH
        or _ID_RE.fullmatch(vocab_id) is None
    ):
        return _DATA_ERROR
    text = _read(vocab_id)
    if text is None:
        return _DATA_ERROR
    return _build(vocab_id, text)


def load(vocab_id: str, /) -> FrozenVocabulary:
    """Load and check one vocabulary from its data file. Raises AsfbError(E_VOCAB_DATA)."""
    outcome = _load(vocab_id)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


@cache
def html_registry() -> VocabRegistry:
    """The five vocabularies the HTML sanitizer declares, loaded once per process."""
    return MappingProxyType({vocab_id: load(vocab_id) for vocab_id in HTML_IDS})


@cache
def registry() -> VocabRegistry:
    """Every vocabulary egress may emit (ALL_IDS), loaded once per process."""
    return MappingProxyType({vocab_id: load(vocab_id) for vocab_id in ALL_IDS})


@cache
def py_registry() -> VocabRegistry:
    """The four Python vocabularies the code sanitizer declares, loaded once per process."""
    return MappingProxyType({vocab_id: load(vocab_id) for vocab_id in PY_IDS})


@cache
def md_registry() -> VocabRegistry:
    """The two vocabularies the markdown sanitizer declares, loaded once per process."""
    return MappingProxyType({vocab_id: load(vocab_id) for vocab_id in MD_IDS})


@cache
def _log_levels() -> FrozenVocabulary:
    return load("log.levels")


def log_level(word: object, /) -> str | None:
    """The canonical log.levels term of a word, or None.

    The one case-insensitive lookup in the vocabularies: ASCII letters only are folded to upper
    case, then the term must match exactly. Nothing else is folded (no NFKC, no casefold), so a
    fullwidth or accented spelling of a level is not a term and stays in the message.
    """
    if type(word) is not str or not word.isascii():
        return None
    term = word.upper()
    return term if term in _log_levels() else None
