"""diff: line ranges of two censored views, the hunk limit, unknown refs and tampered models."""

import json
from importlib import resources
from pathlib import Path

import pytest

from asfb import session as session_module
from asfb.egress import emit, render_diff
from asfb.errors import AsfbError, EgressError, ErrorCode
from asfb.schema import DIFF_RESOURCE
from asfb.schema.diff import DiffV1, Hunk, RefV1
from asfb.schema.doc import Profile
from asfb.session import Diffed, Sanitized, Session
from asfb.types.docid import DocId
from asfb.types.key import SessionKey
from asfb.vocab import registry

KEY = SessionKey(bytes(range(32)))
PAGE_A = "<h1>Welcome Alice</h1><p>contact alice@example.org</p>"


@pytest.fixture
def session(tmp_path: Path) -> Session:
    root = tmp_path / "project"
    root.mkdir()
    (root / "one.html").write_text("<p>one</p>", encoding="utf-8")
    (root / "two.html").write_text("<p>one</p><p>two</p>", encoding="utf-8")
    (root / "page.html").write_text(PAGE_A, encoding="utf-8")
    return Session([str(root)], key=KEY, isolate=False)


def _stored(session: Session, name: str) -> Sanitized:
    result = session.sanitize(name)
    assert isinstance(result, Sanitized)
    return result


def _hunks(diffed: Diffed) -> list[dict[str, object]]:
    hunks: list[dict[str, object]] = json.loads(diffed.json)["hunks"]
    return hunks


def test_a_revision_compared_with_itself_has_no_hunks(session: Session) -> None:
    ref = str(_stored(session, "page.html").ref)
    diffed = session.diff(ref, ref)
    assert _hunks(diffed) == []
    assert str(diffed.view) == f"--- {ref}\n+++ {ref}"


def test_an_edit_gives_one_replace_hunk(session: Session) -> None:
    first = _stored(session, "page.html")
    old = str(first.view).split("\n")[0]  # the first line of the view: the censored h1
    new = old.replace("<h1>", "<h2>").replace("</h1>", "</h2>")
    edited = session.edit(str(first.ref), old, new)
    diffed = session.diff(str(first.ref), str(edited.ref))
    assert _hunks(diffed) == [{"op": "replace", "a_start": 0, "a_end": 1, "b_start": 0, "b_end": 1}]
    assert "-<h1>" in str(diffed.view)
    assert "+<h2>" in str(diffed.view)


def test_a_new_element_in_another_document_is_an_insert(session: Session) -> None:
    one = _stored(session, "one.html")
    two = _stored(session, "two.html")
    diffed = session.diff(str(one.ref), str(two.ref))
    assert _hunks(diffed) == [{"op": "insert", "a_start": 1, "a_end": 1, "b_start": 1, "b_end": 2}]
    assert str(diffed.view).splitlines()[2] == "@@ -2,0 +2,1 @@"
    assert str(diffed.view).splitlines()[3].startswith("+")


def test_unknown_revision_is_doc_unknown(session: Session) -> None:
    ref = str(_stored(session, "page.html").ref)
    doc = ref.split("@")[0]
    with pytest.raises(AsfbError) as info:
        session.diff(f"{doc}@99", ref)
    assert info.value.code is ErrorCode.E_DOC_UNKNOWN


@pytest.mark.parametrize("bad", ["d_0123456789abcdef", "not a ref", "", None])
def test_refs_without_a_revision_are_doc_unknown(session: Session, bad: object) -> None:
    ref = str(_stored(session, "page.html").ref)
    with pytest.raises(AsfbError) as info:
        session.diff(bad, ref)  # type: ignore[arg-type]
    assert info.value.code is ErrorCode.E_DOC_UNKNOWN


def test_more_hunks_than_the_limit_is_refused(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    (root / "many-a.html").write_text("<p>a</p><p>b</p><p>c</p>", encoding="utf-8")
    (root / "many-b.html").write_text("<p>x</p><p>b</p><p>y</p>", encoding="utf-8")
    first = str(_stored(session, "many-a.html").ref)
    second = str(_stored(session, "many-b.html").ref)
    monkeypatch.setattr(session_module, "MAX_HUNKS", 1)
    with pytest.raises(AsfbError) as info:
        session.diff(first, second)
    assert info.value.code is ErrorCode.E_DIFF_LIMIT


def test_the_hunk_limit_allows_exactly_that_many(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    (root / "many-a.html").write_text("<p>a</p><p>b</p><p>c</p>", encoding="utf-8")
    (root / "many-b.html").write_text("<p>x</p><p>b</p><p>y</p>", encoding="utf-8")
    first = str(_stored(session, "many-a.html").ref)
    second = str(_stored(session, "many-b.html").ref)
    monkeypatch.setattr(session_module, "MAX_HUNKS", 2)
    assert len(_hunks(session.diff(first, second))) == 2


def _render_with(stored: Sanitized, hunks: tuple[Hunk, ...]) -> str:
    """Emit a DiffV1 with the given hunks over one stored revision, then render it."""
    model = DiffV1(
        profile=Profile.STRICT,
        vocab=(),
        a=RefV1(doc=DocId(str(stored.ref.doc)), revision=1),
        b=RefV1(doc=DocId(str(stored.ref.doc)), revision=1),
        hunks=hunks,
    )
    emitted = emit(model, vocab=registry())
    return str(render_diff(emitted, stored.json, stored.json))


def test_a_well_formed_hunk_on_the_same_view_renders(session: Session) -> None:
    stored = _stored(session, "page.html")
    rendered = _render_with(stored, (Hunk(op="replace", a_start=0, a_end=1, b_start=0, b_end=1),))
    assert rendered.count("\n@@ ") == 1


@pytest.mark.parametrize(
    "hunk",
    [
        pytest.param(
            Hunk(op="replace", a_start=0, a_end=99, b_start=0, b_end=1), id="range-past-the-view"
        ),
        pytest.param(
            Hunk(op="insert", a_start=0, a_end=1, b_start=0, b_end=1), id="insert-with-a-range"
        ),
        pytest.param(
            Hunk(op="delete", a_start=0, a_end=1, b_start=0, b_end=1), id="delete-with-b-range"
        ),
        pytest.param(
            Hunk(op="replace", a_start=0, a_end=0, b_start=0, b_end=1), id="replace-empty-a"
        ),
    ],
)
def test_a_tampered_diff_model_is_not_rendered(session: Session, hunk: Hunk) -> None:
    stored = _stored(session, "page.html")
    with pytest.raises(EgressError) as info:
        _render_with(stored, (hunk,))
    assert info.value.code is ErrorCode.E_VIEW
    assert info.value.args == (ErrorCode.E_VIEW.value,)
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


def test_committed_diff_schema_equals_the_model() -> None:
    committed = resources.files("asfb.schema").joinpath(DIFF_RESOURCE).read_text(encoding="utf-8")
    assert json.loads(committed) == DiffV1.model_json_schema()
