"""edit: the text rules, token matching, staleness, newline and BOM handling, and atomic writes.

Documents are censored in process unless a test says otherwise. Tokens are minted with the test's
key, exactly as the session mints them, so the agent-side text is built from the same tokens.
"""

import codecs
import os
from pathlib import Path

import pytest

from asfb import session as session_module
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.tokens import token_for
from asfb.session import Sanitized, Session
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted

KEY = SessionKey(bytes(range(32)))
PAGE_A = "<h1>Welcome Alice</h1><p>contact alice@example.org</p>"
DUPLICATE = "<p>Nested bob</p><p>Nested bob</p>"
MIXED = "<p>Alpha</p><p>Tom &amp; Jerry</p>"
UNKNOWN = "t_0123456789abcdef"


def _tok(text: str) -> str:
    return str(token_for(KEY, Untrusted(text)))


def _store(tmp_path: Path, content: bytes, *, isolate: bool = False) -> tuple[Session, Path, str]:
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "page.html"
    path.write_bytes(content)
    session = Session([str(root)], key=KEY, isolate=isolate)
    stored = session.sanitize("page.html")
    assert isinstance(stored, Sanitized)
    return session, path, str(stored.ref)


def _code(session: Session, ref: str, old: str, new: str) -> ErrorCode:
    with pytest.raises(AsfbError) as info:
        session.edit(ref, old, new)
    return info.value.code


def test_replaces_one_occurrence_and_stores_a_new_revision(tmp_path: Path) -> None:
    session, path, ref = _store(tmp_path, PAGE_A.encode())
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    result = session.edit(ref, old, f"<h2>{_tok('Welcome Alice')}</h2>")
    assert result.ref.revision == 2
    assert path.read_text(encoding="utf-8") == PAGE_A.replace("<h1>", "<h2>", 1).replace(
        "</h1>", "</h2>", 1
    )
    assert "<h2>Welcome Alice</h2>" not in str(result.view)
    assert "h2" in str(result.view)


def test_an_older_revision_is_stale_after_an_edit(tmp_path: Path) -> None:
    session, _path, ref = _store(tmp_path, PAGE_A.encode())
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    session.edit(ref, old, f"<h2>{_tok('Welcome Alice')}</h2>")
    assert _code(session, ref, old, old) is ErrorCode.E_EDIT_STALE


def test_plain_words_are_refused_in_both_texts(tmp_path: Path) -> None:
    session, path, ref = _store(tmp_path, PAGE_A.encode())
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    assert _code(session, ref, old, "<h2>Alice</h2>") is ErrorCode.E_EDIT_INVALID_TEXT
    assert _code(session, ref, "<h1>Alice</h1>", "<h2>x</h2>") is ErrorCode.E_EDIT_INVALID_TEXT
    assert path.read_bytes() == PAGE_A.encode()


@pytest.mark.parametrize(
    ("old", "new"),
    [pytest.param("", "<h2>x</h2>", id="empty-old"), pytest.param("same", "same", id="unchanged")],
)
def test_empty_or_unchanged_text_is_refused(tmp_path: Path, old: str, new: str) -> None:
    session, _path, ref = _store(tmp_path, PAGE_A.encode())
    assert _code(session, ref, old, new) is ErrorCode.E_EDIT_INVALID_TEXT


def test_non_ascii_text_is_refused(tmp_path: Path) -> None:
    session, path, ref = _store(tmp_path, PAGE_A.encode())
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    assert _code(session, ref, old + "é", old) is ErrorCode.E_EDIT_INVALID_TEXT
    assert _code(session, ref, old, f"<h2>{_tok('Welcome Alice')}</h2>\u00a0") is (
        ErrorCode.E_EDIT_INVALID_TEXT
    )
    assert path.read_bytes() == PAGE_A.encode()


def test_unknown_token_is_refused(tmp_path: Path) -> None:
    session, _path, ref = _store(tmp_path, PAGE_A.encode())
    assert _code(session, ref, f"<h1>{UNKNOWN}</h1>", f"<h2>{UNKNOWN}</h2>") is (
        ErrorCode.E_EDIT_UNKNOWN_TOKEN
    )


def test_new_token_that_was_never_seen_is_refused(tmp_path: Path) -> None:
    session, _path, ref = _store(tmp_path, PAGE_A.encode())
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    assert _code(session, ref, old, f"<h2>{UNKNOWN}</h2>") is ErrorCode.E_EDIT_UNKNOWN_TOKEN


def test_text_without_a_match_is_refused(tmp_path: Path) -> None:
    session, path, ref = _store(tmp_path, PAGE_A.encode())
    new = f"<h2>{_tok('Welcome Alice')}</h2>"
    old = f"<h1>{_tok('Welcome Alice')}</h2>"
    assert _code(session, ref, old, new) is ErrorCode.E_EDIT_NO_MATCH
    assert path.read_bytes() == PAGE_A.encode()


def test_two_matches_are_ambiguous_and_the_file_is_untouched(tmp_path: Path) -> None:
    session, path, ref = _store(tmp_path, DUPLICATE.encode())
    old = f"<p>{_tok('Nested bob')}</p>"
    assert _code(session, ref, old, f"<p>{_tok('Nested bob')}</p>\n") is (
        ErrorCode.E_EDIT_AMBIGUOUS
    )
    assert path.read_bytes() == DUPLICATE.encode()


def test_markup_characters_in_a_surface_are_refused_in_html(tmp_path: Path) -> None:
    # "Tom & Jerry" decodes from &amp;; inserting the decoded surface would create markup.
    session, path, ref = _store(tmp_path, MIXED.encode())
    old = f"<p>{_tok('Alpha')}</p>"
    assert _code(session, ref, old, f"<p>{_tok('Tom & Jerry')}</p>") is (
        ErrorCode.E_EDIT_INVALID_TEXT
    )
    assert path.read_bytes() == MIXED.encode()


def test_a_stale_ref_is_refused_when_the_file_changed_on_disk(tmp_path: Path) -> None:
    session, path, ref = _store(tmp_path, PAGE_A.encode())
    path.write_text("<p>someone else</p>", encoding="utf-8")
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    assert _code(session, ref, old, f"<h2>{_tok('Welcome Alice')}</h2>") is (ErrorCode.E_EDIT_STALE)


def test_result_over_the_size_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, path, ref = _store(tmp_path, PAGE_A.encode())
    monkeypatch.setattr(session_module, "MAX_BYTES", len(PAGE_A.encode()) + 4)
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    grown = f"<h1>{_tok('Welcome Alice')}</h1><p>{_tok('Welcome Alice')}</p>"
    assert _code(session, ref, old, grown) is ErrorCode.E_EDIT_TOO_LARGE
    assert path.read_bytes() == PAGE_A.encode()


def test_unparseable_result_leaves_the_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, path, ref = _store(tmp_path, PAGE_A.encode())

    def refuse(self: Session, kind: str, text: str, name: str | None) -> None:
        raise AsfbError(ErrorCode.E_HTML_PARSE)

    monkeypatch.setattr(Session, "_censor", refuse)
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    assert _code(session, ref, old, f"<h2>{_tok('Welcome Alice')}</h2>") is (ErrorCode.E_HTML_PARSE)
    assert path.read_bytes() == PAGE_A.encode()
    stored = session.sanitize(ref)
    assert isinstance(stored, Sanitized)
    assert stored.ref.revision == 1


def test_failed_replace_is_a_write_error_and_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, path, ref = _store(tmp_path, PAGE_A.encode())

    def refuse(src: str, dst: str) -> None:
        raise OSError("refused")

    monkeypatch.setattr(os, "replace", refuse)
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    assert _code(session, ref, old, f"<h2>{_tok('Welcome Alice')}</h2>") is ErrorCode.E_EDIT_WRITE
    assert path.read_bytes() == PAGE_A.encode()
    assert [item.name for item in path.parent.iterdir()] == ["page.html"]


def test_byte_order_mark_is_kept(tmp_path: Path) -> None:
    session, path, ref = _store(tmp_path, codecs.BOM_UTF8 + PAGE_A.encode())
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    session.edit(ref, old, f"<h2>{_tok('Welcome Alice')}</h2>")
    data = path.read_bytes()
    assert data.startswith(codecs.BOM_UTF8)
    edited = PAGE_A.replace("<h1>", "<h2>", 1).replace("</h1>", "</h2>", 1)
    assert data == codecs.BOM_UTF8 + edited.encode()


def test_crlf_files_keep_crlf_endings(tmp_path: Path) -> None:
    session, path, ref = _store(tmp_path, b"<p>Alpha</p>\r\n<p>Bob</p>\r\n")
    old = f"<p>{_tok('Alpha')}</p>\n<p>{_tok('Bob')}</p>"
    new = f"<p>{_tok('Bob')}</p>\n<p>{_tok('Alpha')}</p>"
    session.edit(ref, old, new)
    data = path.read_bytes()
    assert data == b"<p>Bob</p>\r\n<p>Alpha</p>\r\n"
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_unknown_or_malformed_refs_are_doc_unknown(tmp_path: Path) -> None:
    session, _path, _ref = _store(tmp_path, PAGE_A.encode())
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    assert _code(session, "d_0123456789abcdef@1", old, old) is ErrorCode.E_DOC_UNKNOWN
    assert _code(session, "page.html", old, old) is ErrorCode.E_DOC_UNKNOWN


@pytest.mark.skipif(os.name != "posix", reason="temporary file names are checked on POSIX")
def test_a_successful_edit_leaves_no_temporary_file(tmp_path: Path) -> None:
    session, path, ref = _store(tmp_path, PAGE_A.encode())
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    session.edit(ref, old, f"<h2>{_tok('Welcome Alice')}</h2>")
    assert [item.name for item in path.parent.iterdir()] == ["page.html"]


def test_isolated_edit_matches_the_in_process_result(tmp_path: Path) -> None:
    direct_session, direct_path, direct_ref = _store(tmp_path / "direct", PAGE_A.encode())
    isolated_session, isolated_path, isolated_ref = _store(
        tmp_path / "isolated", PAGE_A.encode(), isolate=True
    )
    old = f"<h1>{_tok('Welcome Alice')}</h1>"
    new = f"<h2>{_tok('Welcome Alice')}</h2>"
    direct = direct_session.edit(direct_ref, old, new)
    isolated = isolated_session.edit(isolated_ref, old, new)
    assert direct.json == isolated.json
    assert str(direct.view) == str(isolated.view)
    assert direct_path.read_bytes() == isolated_path.read_bytes()
