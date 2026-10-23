"""The listing schema (asfb.list/1): committed file, bounds, DocId pattern and the closed set."""

import json
from importlib import resources
from typing import Any

import pytest
from pydantic import ValidationError

from asfb.schema import LIST_RESOURCE
from asfb.schema.doc import Profile
from asfb.schema.list import ListV1
from asfb.types.docid import DocId

DOC = "d_0123456789abcdef"
SAMPLE_ID = "t_0123456789abcdef"
ENTRY: dict[str, Any] = {"doc": DOC, "path": [SAMPLE_ID], "ext": "html"}
BASE: dict[str, Any] = {"profile": "strict", "vocab": [], "entries": []}


def _parse(payload: dict[str, Any]) -> ListV1:
    return ListV1.model_validate_json(json.dumps(payload), strict=True)


def _committed() -> str:
    return resources.files("asfb.schema").joinpath(LIST_RESOURCE).read_text(encoding="utf-8")


def test_committed_schema_equals_model() -> None:
    assert json.loads(_committed()) == ListV1.model_json_schema()


def test_committed_schema_bytes_are_canonical() -> None:
    expected = json.dumps(ListV1.model_json_schema(), indent=2, sort_keys=True) + "\n"
    assert _committed() == expected


def test_accepts_listing_and_roundtrips() -> None:
    doc = _parse(
        {**BASE, "entries": [ENTRY, {**ENTRY, "ext": "htm", "path": [SAMPLE_ID, SAMPLE_ID]}]}
    )
    assert doc.profile is Profile.STRICT
    assert len(doc.entries) == 2
    assert doc.entries[1].ext == "htm"


def test_entries_bound_is_ten_thousand() -> None:
    assert _parse({**BASE, "entries": [ENTRY] * 10_000}).entries[-1].doc == DOC
    with pytest.raises(ValidationError):
        _parse({**BASE, "entries": [ENTRY] * 10_001})


def test_path_bound_is_sixty_four() -> None:
    assert (
        len(_parse({**BASE, "entries": [{**ENTRY, "path": [SAMPLE_ID] * 64}]}).entries[0].path)
        == 64
    )
    with pytest.raises(ValidationError):
        _parse({**BASE, "entries": [{**ENTRY, "path": [SAMPLE_ID] * 65}]})


def test_vocabulary_must_be_empty() -> None:
    with pytest.raises(ValidationError):
        _parse({**BASE, "vocab": [{"id": "test.stub", "version": 1}]})


@pytest.mark.parametrize(
    "doc",
    [
        "d_0123456789ABCDEF",
        "d_0123456789abcde",
        "d_0123456789abcdef0",
        "t_0123456789abcdef",
        "d_0123456789abcdef\n",
        "d_xyzxyzxyzxyzxyzx",
    ],
)
def test_doc_id_pattern_is_enforced(doc: str) -> None:
    with pytest.raises(ValidationError):
        _parse({**BASE, "entries": [{**ENTRY, "doc": doc}]})


@pytest.mark.parametrize(
    "ext", ["html", "htm", "txt", "md", "markdown", "py", "pyi", "log", "jsonl", "ndjson"]
)
def test_ext_accepts_the_document_suffixes(ext: str) -> None:
    assert _parse({**BASE, "entries": [{**ENTRY, "ext": ext}]}).entries[0].ext == ext


@pytest.mark.parametrize("ext", ["pyc", "pdf", "HTML", "htm ", "", "md.txt", None])
def test_ext_is_a_closed_set(ext: object) -> None:
    with pytest.raises(ValidationError):
        _parse({**BASE, "entries": [{**ENTRY, "ext": ext}]})


@pytest.mark.parametrize(
    "payload",
    [
        {**BASE, "extra": 1},
        {**BASE, "schema_id": "asfb.list/2"},
        {**BASE, "entries": [{**ENTRY, "extra": 1}]},
        {**BASE, "entries": [{"doc": DOC, "path": [SAMPLE_ID]}]},
        {**BASE, "entries": [{**ENTRY, "path": ["hello.txt"]}]},
        {**BASE, "entries": [{**ENTRY, "path": "t_0123456789abcdef"}]},
        {**BASE, "entries": ["d_0123456789abcdef"]},
        {**BASE, "profile": "STRICT"},
    ],
)
def test_closed_set_refuses_everything_else(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _parse(payload)


def test_doc_id_type_is_strict_like_token() -> None:
    assert DocId(DOC) == DOC
    for bad in ("d_0123456789ABCDEF", "d_0123456789abcde", "t_0123456789abcdef"):
        with pytest.raises(ValueError, match="invalid doc id format"):
            DocId(bad)
    with pytest.raises(TypeError, match="exact str"):
        DocId(DOC.encode())  # type: ignore[arg-type]
