"""Egress for the listing schema (ListV1): the allowed set, its strings and the attack cases."""

import json
from typing import Any, cast

import pytest
from pydantic import SerializerFunctionWrapHandler, model_serializer

from asfb.egress import gate
from asfb.egress.gate import emit
from asfb.errors import EgressError, ErrorCode
from asfb.schema.doc import DocV1, Profile, SourceKind
from asfb.schema.list import ListEntry, ListV1
from asfb.types.docid import DocId
from asfb.types.token import Token
from tests.stubs import STUB_VOCAB

DOC = DocId("d_0123456789abcdef")
TOKEN = Token("t_0123456789abcdef")
REGISTRY = {STUB_VOCAB.id: STUB_VOCAB}
ENTRY = ListEntry(doc=DOC, path=(TOKEN, TOKEN), ext="html")
LISTING = ListV1(profile=Profile.STRICT, vocab=(), entries=(ENTRY,))


class _SubListing(ListV1):
    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {"schema_id": "asfb.list/1", "profile": "strict", "vocab": [], "entries": []}


def _raw_listing(entries: tuple[Any, ...], *, vocab: tuple[Any, ...] = ()) -> ListV1:
    return ListV1.model_construct(profile=Profile.STRICT, vocab=vocab, entries=entries)


def _raw_entry(**fields: Any) -> ListEntry:
    base: dict[str, Any] = {"doc": DOC, "path": (TOKEN,), "ext": "html"}
    base.update(fields)
    return ListEntry.model_construct(**base)


def _assert_code(doc: object, code: ErrorCode) -> None:
    with pytest.raises(EgressError) as info:
        emit(cast(ListV1, doc), vocab=REGISTRY)
    assert info.value.code is code
    assert info.value.args == (code.value,)
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


def test_emits_canonical_listing() -> None:
    out = emit(LISTING, vocab=REGISTRY)
    assert json.loads(out) == {
        "schema_id": "asfb.list/1",
        "profile": "strict",
        "vocab": [],
        "entries": [
            {"doc": "d_0123456789abcdef", "path": ["t_0123456789abcdef"] * 2, "ext": "html"}
        ],
    }
    assert ListV1.model_validate_json(out, strict=True) == LISTING


def test_empty_listing_emits() -> None:
    empty = ListV1(profile=Profile.DIFFABLE, vocab=(), entries=())
    assert json.loads(emit(empty, vocab=REGISTRY))["entries"] == []


def test_listing_allowlists_come_from_its_schema() -> None:
    assert gate._SPECS[ListV1].keys == frozenset(  # noqa: SLF001
        {
            "schema_id",
            "profile",
            "vocab",
            "entries",
            "doc",
            "path",
            "ext",
            "id",
            "version",
        }
    )
    assert gate._SPECS[ListV1].words == frozenset(  # noqa: SLF001
        {
            "asfb.list/1",
            "strict",
            "diffable",
            "html",
            "htm",
            "txt",
            "md",
            "markdown",
            "py",
            "pyi",
            "log",
            "jsonl",
            "ndjson",
            "db",
            "sqlite",
            "sqlite3",
        }
    )


def test_doc_id_is_allowed_for_listings_only() -> None:
    word = str(DOC)
    assert gate._strings_ok([word], {}, gate._SPECS[ListV1]) is True  # noqa: SLF001
    assert gate._strings_ok([word], {}, gate._SPECS[DocV1]) is False  # noqa: SLF001


def test_raw_string_in_path_is_refused() -> None:
    entry = _raw_entry(path=("hello",))
    _assert_code(_raw_listing((entry,)), ErrorCode.E_EGRESS_SCHEMA)


def test_bare_doc_id_in_path_is_refused() -> None:
    entry = _raw_entry(path=(str(DOC),))
    _assert_code(_raw_listing((entry,)), ErrorCode.E_EGRESS_SCHEMA)


@pytest.mark.parametrize(
    "doc",
    [
        pytest.param("d_0123456789ABCDEF", id="upper-hex"),
        pytest.param("d_0123456789abcde", id="short"),
        pytest.param("d_0123456789abcdef0", id="long"),
        pytest.param("t_0123456789abcdef", id="token-prefix"),
        pytest.param("d_0123456789abcdef\n", id="trailing-newline"),
    ],
)
def test_bad_doc_id_is_refused(doc: str) -> None:
    entry = _raw_entry(doc=doc)
    _assert_code(_raw_listing((entry,)), ErrorCode.E_EGRESS_SCHEMA)


@pytest.mark.parametrize("ext", ["pdf", "pyc", "HTML", "", ".py", " md"])
def test_other_extensions_are_refused(ext: str) -> None:
    _assert_code(_raw_listing((_raw_entry(ext=ext),)), ErrorCode.E_EGRESS_SCHEMA)


def test_subclass_is_refused_as_type() -> None:
    sub = _SubListing(profile=Profile.STRICT, vocab=(), entries=())
    _assert_code(sub, ErrorCode.E_EGRESS_TYPE)


def test_document_is_not_a_listing_and_listing_is_not_a_document() -> None:
    doc = DocV1(profile=Profile.STRICT, source=SourceKind.HTML, vocab=(), body=())
    assert json.loads(emit(doc, vocab=REGISTRY))["schema_id"] == "asfb.doc/1"
    _assert_code(_raw_listing(("not an entry",)), ErrorCode.E_EGRESS_SERIALIZE)


def test_entries_over_the_bound_are_refused() -> None:
    _assert_code(_raw_listing((ENTRY,) * 10_001), ErrorCode.E_EGRESS_SCHEMA)


def test_path_over_the_bound_is_refused() -> None:
    _assert_code(_raw_listing((_raw_entry(path=(TOKEN,) * 65),)), ErrorCode.E_EGRESS_SCHEMA)


def test_listing_with_declared_vocabulary_is_refused() -> None:
    from asfb.schema.doc import VocabRef

    ref = VocabRef(id=STUB_VOCAB.id, version=1)
    _assert_code(_raw_listing((), vocab=(ref,)), ErrorCode.E_EGRESS_SCHEMA)


def test_listing_validator_refuses_extra_keys() -> None:
    instance = json.loads(emit(LISTING, vocab=REGISTRY))
    assert gate._SPECS[ListV1].validator(instance) is True  # noqa: SLF001
    instance["entries"][0]["extra"] = 1
    assert gate._SPECS[ListV1].validator(instance) is False  # noqa: SLF001
