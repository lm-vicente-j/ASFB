"""Censored view of a listing: one line per entry, in the order given."""

import json
from typing import Any

import pytest

from asfb.egress import Emitted, emit, render
from asfb.errors import EgressError, ErrorCode
from asfb.schema.doc import Profile
from asfb.schema.list import ListEntry, ListV1
from asfb.types.docid import DocId
from asfb.types.token import Token
from asfb.vocab import html_registry

FIRST = ListEntry(
    doc=DocId("d_0123456789abcdef"),
    path=(Token("t_aaaaaaaaaaaaaaaa"), Token("t_bbbbbbbbbbbbbbbb")),
    ext="html",
)
SECOND = ListEntry(doc=DocId("d_fedcba9876543210"), path=(Token("t_cccccccccccccccc"),), ext="htm")


def _emitted(*entries: ListEntry) -> Emitted:
    return emit(ListV1(profile=Profile.STRICT, vocab=(), entries=entries), vocab=html_registry())


def test_listing_renders_one_line_per_entry_in_given_order() -> None:
    view = render(_emitted(SECOND, FIRST))
    assert str(view) == (
        "d_fedcba9876543210 t_cccccccccccccccc.htm\n"
        "d_0123456789abcdef t_aaaaaaaaaaaaaaaa/t_bbbbbbbbbbbbbbbb.html"
    )


def test_empty_listing_renders_empty_view() -> None:
    assert str(render(_emitted())) == ""


def test_listing_view_is_the_emitted_listing_without_paths_or_text() -> None:
    view = str(render(_emitted(FIRST)))
    assert view == "d_0123456789abcdef t_aaaaaaaaaaaaaaaa/t_bbbbbbbbbbbbbbbb.html"
    assert "/" in view
    assert "\\" not in view


def test_tampered_listing_is_refused() -> None:
    raw = json.loads(_emitted(FIRST))
    raw["entries"][0]["extra"] = 1
    tampered = Emitted(json.dumps(raw, separators=(",", ":")))
    with pytest.raises(EgressError) as info:
        render(tampered)
    assert info.value.code is ErrorCode.E_VIEW
    assert info.value.args == ("E_VIEW",)


def test_non_canonical_spacing_is_refused() -> None:
    spaced = Emitted(_emitted(FIRST).replace(",", ", "))
    with pytest.raises(EgressError) as info:
        render(spaced)
    assert info.value.code is ErrorCode.E_VIEW


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_id": "asfb.list/1", "profile": "strict", "vocab": [], "entries": [1]},
        {"schema_id": "asfb.list/9", "profile": "strict", "vocab": [], "entries": []},
        [],
        "not json",
    ],
)
def test_unknown_or_malformed_input_is_refused(payload: Any) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    with pytest.raises(EgressError) as info:
        render(Emitted(text))
    assert info.value.code is ErrorCode.E_VIEW
