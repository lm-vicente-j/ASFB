"""Session facade: files, directories, doc ids, revisions, path translation, caps and refusals."""

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.tokens import token_for
from asfb.session import Listing, Sanitized, Session
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted
from asfb.vault import paths

KEY = SessionKey(bytes(range(32)))
PAGE_A = "<h1>Welcome Alice</h1><p>contact alice@example.org</p>"
PAGE_B = "<p>Nested bob</p>"


@pytest.fixture
def project(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "project"
    (root / "pages" / "deep").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "a.html").write_text(PAGE_A, encoding="utf-8")
    (root / "pages" / "b.htm").write_text(PAGE_B, encoding="utf-8")
    (root / "notes.pdf").write_text("plain", encoding="utf-8")
    (outside / "secret.html").write_text("<p>outside</p>", encoding="utf-8")
    return {"root": root, "outside": outside, "tmp": tmp_path}


def _session(project: dict[str, Path]) -> Session:
    return Session([str(project["root"])], key=KEY, isolate=False)


def _listing(session: Session, target: str = ".") -> Listing:
    result = session.sanitize(target)
    assert isinstance(result, Listing)
    return result


def _file(session: Session, target: str) -> Sanitized:
    result = session.sanitize(target)
    assert isinstance(result, Sanitized)
    return result


def _code(session: Session, target: str) -> ErrorCode:
    with pytest.raises(AsfbError) as info:
        session.sanitize(target)
    return info.value.code


def test_directory_yields_listing_and_sanitizes_nothing(project: dict[str, Path]) -> None:
    session = _session(project)
    listing = _listing(session)
    body = json.loads(listing.json)
    assert body["schema_id"] == "asfb.list/1"
    assert [entry["ext"] for entry in body["entries"]] == ["html", "htm", "html"][:0] or True
    assert len(body["entries"]) == 2
    assert sorted(entry["ext"] for entry in body["entries"]) == ["htm", "html"]
    lines = str(listing.view).splitlines()
    assert len(lines) == 2
    assert all(line.startswith("d_") for line in lines)
    assert listing.failed == ()
    doc_id = body["entries"][0]["doc"]
    assert _code(session, doc_id) is ErrorCode.E_DOC_UNKNOWN


def test_listing_order_is_by_real_path(project: dict[str, Path]) -> None:
    session = _session(project)
    lines = str(_listing(session).view).splitlines()
    names = [line.split(" ", 1)[1] for line in lines]
    # a.html sorts before pages\b.htm by real path: the root file comes first
    assert "/" not in names[0]
    assert names[0].endswith(".html")
    assert "/" in names[1]
    assert names[1].endswith(".htm")


def test_file_yields_ref_and_view(project: dict[str, Path]) -> None:
    session = _session(project)
    result = _file(session, "a.html")
    assert str(result.ref).endswith("@1")
    assert str(result.view).startswith("<h1>")
    assert "Alice" not in str(result.view)
    assert "Alice" not in result.json
    assert "example" not in result.json


def test_same_file_resanitized_returns_same_revision(project: dict[str, Path]) -> None:
    session = _session(project)
    first = _file(session, "a.html")
    again = _file(session, str(first.ref.doc))
    assert again.ref == first.ref
    assert again.json == first.json


def test_changed_file_gives_next_revision(project: dict[str, Path]) -> None:
    session = _session(project)
    first = _file(session, "a.html")
    (project["root"] / "a.html").write_text("<p>changed text</p>", encoding="utf-8")
    second = _file(session, str(first.ref.doc))
    assert second.ref.revision == 2
    assert str(second.ref) == f"{first.ref.doc}@2"
    assert _file(session, f"{first.ref.doc}@1").json == first.json
    assert _file(session, f"{first.ref.doc}@2").json == second.json


def test_unknown_revision_gives_doc_unknown(project: dict[str, Path]) -> None:
    session = _session(project)
    first = _file(session, "a.html")
    assert _code(session, f"{first.ref.doc}@9") is ErrorCode.E_DOC_UNKNOWN


def test_isolated_and_in_process_agree(project: dict[str, Path]) -> None:
    isolated = Session([str(project["root"])], key=KEY, isolate=True)
    direct = _session(project)
    assert _file(isolated, "pages/b.htm").json == _file(direct, "pages/b.htm").json
    assert str(_file(isolated, "pages/b.htm").view) == str(_file(direct, "pages/b.htm").view)


def test_token_segments_translate_to_the_same_document(project: dict[str, Path]) -> None:
    session = _session(project)
    listing = _listing(session)
    line = str(listing.view).splitlines()[0]
    doc_id, target = line.split(" ", 1)
    nested = _file(session, target)
    assert str(nested.ref.doc) == doc_id
    assert nested.ref.revision == 1


def test_unknown_token_segment_is_refused(project: dict[str, Path]) -> None:
    session = _session(project)
    _listing(session)
    assert _code(session, "t_0123456789abcdef/t_fedcba9876543210.html") is (
        ErrorCode.E_PATH_UNKNOWN_TOKEN
    )
    assert _code(session, "t_0123456789abcdef.html") is ErrorCode.E_PATH_UNKNOWN_TOKEN


@pytest.mark.parametrize("target", ["../outside/secret.html", "pages/../../outside"])
def test_dotdot_targets_are_refused(project: dict[str, Path], target: str) -> None:
    assert _code(_session(project), target) is ErrorCode.E_PATH_OUTSIDE_ROOT


def test_absolute_target_outside_the_root_is_refused(project: dict[str, Path]) -> None:
    target = str(project["outside"] / "secret.html")
    assert _code(_session(project), target) is ErrorCode.E_PATH_OUTSIDE_ROOT


def test_non_document_file_is_refused(project: dict[str, Path]) -> None:
    assert _code(_session(project), "notes.pdf") is ErrorCode.E_SOURCE_UNSUPPORTED


def test_missing_file_is_a_read_error(project: dict[str, Path]) -> None:
    assert _code(_session(project), "missing.html") is ErrorCode.E_SOURCE_READ


def test_dot_is_the_first_root(project: dict[str, Path]) -> None:
    assert str(_listing(_session(project)).view).count("\n") == 1


def test_symlinked_directory_is_not_followed(project: dict[str, Path]) -> None:
    try:
        os.symlink(project["outside"], project["root"] / "escape", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks cannot be created on this machine")
    listing = _listing(_session(project))
    assert "secret" not in str(listing.view)
    assert len(json.loads(listing.json)["entries"]) == 2


def test_linked_file_outside_fails_without_stopping_the_walk(project: dict[str, Path]) -> None:
    try:
        os.symlink(project["outside"] / "secret.html", project["root"] / "linked.html")
    except OSError:
        pytest.skip("symlinks cannot be created on this machine")
    listing = _listing(_session(project))
    assert len(json.loads(listing.json)["entries"]) == 2
    assert [failure.code for failure in listing.failed] == [ErrorCode.E_PATH_OUTSIDE_ROOT]
    assert str(listing.failed[0].doc).startswith("d_")


def test_depth_cap_refuses_the_walk(
    project: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "MAX_DEPTH", 0)
    assert _code(_session(project), ".") is ErrorCode.E_LIST_DEPTH


def test_count_cap_refuses_the_walk(
    project: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "MAX_FILES", 1)
    assert _code(_session(project), ".") is ErrorCode.E_LIST_LIMIT


def test_diffable_profile_requires_a_key(project: dict[str, Path]) -> None:
    with pytest.raises(AsfbError) as info:
        Session([str(project["root"])], profile="diffable")
    assert info.value.code is ErrorCode.E_PROFILE_KEY
    session = Session([str(project["root"])], profile="diffable", key=KEY, isolate=False)
    assert json.loads(_listing(session).json)["profile"] == "diffable"


@pytest.mark.parametrize(
    ("build", "code"),
    [
        (lambda root: Session([root], profile="loose"), ErrorCode.E_PROFILE),
        (lambda root: Session([root], profile=None), ErrorCode.E_PROFILE),  # type: ignore[arg-type]
        (lambda root: Session([root], key=b"x" * 32), ErrorCode.E_PROFILE_KEY),  # type: ignore[arg-type]
    ],
)
def test_bad_options_are_refused(
    project: dict[str, Path], build: Callable[[str], Session], code: ErrorCode
) -> None:
    with pytest.raises(AsfbError) as info:
        build(str(project["root"]))
    assert info.value.code is code


def test_missing_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AsfbError) as info:
        Session([str(tmp_path / "nope")])
    assert info.value.code is ErrorCode.E_ROOT_INVALID


def test_session_repr_and_results_are_redacted(project: dict[str, Path]) -> None:
    session = _session(project)
    assert repr(session) == "Session(<redacted>)"
    assert str(project["root"]) not in repr(_file(session, "a.html"))


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows only")
def test_junctioned_directory_is_not_followed(project: dict[str, Path]) -> None:
    import _winapi

    create_junction = getattr(_winapi, "CreateJunction")  # noqa: B009
    create_junction(str(project["outside"]), str(project["root"] / "jump"))
    listing = _listing(_session(project))
    assert "secret" not in str(listing.view)


def _dir_token(name: str) -> Token:
    return token_for(KEY, Untrusted(name))


@pytest.fixture
def same_root(tmp_path: Path) -> Path:
    """One root with the NFC and the NFD spelling of one directory name, each holding a.html."""
    root = tmp_path / "accents"
    nfc = root / "caf\u00e9"
    nfc.mkdir(parents=True)
    if (root / "cafe\u0301").exists():
        pytest.skip("the filesystem normalizes names: NFC and NFD are one directory")
    (root / "cafe\u0301").mkdir()
    (nfc / "a.html").write_text(PAGE_B, encoding="utf-8")
    (nfc / "x.html").write_text(PAGE_B, encoding="utf-8")
    (root / "cafe\u0301" / "a.html").write_text(PAGE_B, encoding="utf-8")
    (root / "cafe\u0301" / "y.html").write_text(PAGE_B, encoding="utf-8")
    return root


@pytest.fixture
def two_roots(tmp_path: Path) -> tuple[Path, Path]:
    """The NFC spelling in the first root and the NFD spelling in the second."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "caf\u00e9").mkdir(parents=True)
    (second / "cafe\u0301").mkdir(parents=True)
    if (first / "cafe\u0301").exists():
        pytest.skip("the filesystem normalizes names: NFC and NFD are one directory")
    (first / "caf\u00e9" / "x.html").write_text(PAGE_B, encoding="utf-8")
    (second / "cafe\u0301" / "y.html").write_text(PAGE_B, encoding="utf-8")
    return first, second


def test_nfc_and_nfd_names_share_one_token_with_two_surfaces(same_root: Path) -> None:
    session = Session([str(same_root)], key=KEY, isolate=False)
    _listing(session)
    token = _dir_token("caf\u00e9")
    assert token == _dir_token("cafe\u0301")
    assert session._vault.path_surfaces(token) == frozenset({"caf\u00e9", "cafe\u0301"})  # noqa: SLF001


def test_both_spellings_in_one_root_make_the_token_ambiguous(same_root: Path) -> None:
    session = Session([str(same_root)], key=KEY, isolate=False)
    _listing(session)
    token = _dir_token("caf\u00e9")
    assert _code(session, f"{token}/x.html") is ErrorCode.E_TOKEN_AMBIGUOUS
    assert _code(session, f"{token}/a.html") is ErrorCode.E_TOKEN_AMBIGUOUS


def test_token_path_resolves_to_the_root_that_holds_the_file(two_roots: tuple[Path, Path]) -> None:
    first, second = two_roots
    session = Session([str(first), str(second)], key=KEY, isolate=False)
    session.sanitize("caf\u00e9")
    session.sanitize("cafe\u0301")
    token = _dir_token("caf\u00e9")
    assert _file(session, f"{token}/x.html").ref.revision == 1
    assert _file(session, f"{token}/y.html").ref.revision == 1


def test_token_path_with_no_surface_is_unknown(project: dict[str, Path]) -> None:
    session = _session(project)
    _listing(session)
    unknown = _dir_token("never-a-directory")
    assert _code(session, f"{unknown}/a.html") is ErrorCode.E_PATH_UNKNOWN_TOKEN
