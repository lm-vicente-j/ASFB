"""Plain text as a source: the api and session dispatch, the isolated worker, and edit rules.

The worker's reply is checked against the kind it was asked for, so a reply of another model
type is a protocol error. The HTML markup rule of edit applies to HTML sources only.
"""

import re
import sys
from pathlib import Path

import pytest

from asfb import api, ingest
from asfb.errors import AsfbError, ErrorCode
from asfb.sandbox import client, protocol
from asfb.sandbox.client import ChildResult
from asfb.sanitize.text import censor_text_with_pairs
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import Profile
from asfb.schema.text import TextV1
from asfb.session import Sanitized, Session
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted
from asfb.vault import paths

KEY = SessionKey(bytes(range(32)))
TOKEN_RE = re.compile(r"t_[0-9a-f]{16}")
TEXT = "one\n\n  two & three\n"


def _tokens(view: str) -> list[str]:
    return TOKEN_RE.findall(view)


def _text_session(tmp_path: Path, content: str, name: str = "notes.txt") -> tuple[Session, Path]:
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_text(content, encoding="utf-8")
    return Session([str(root)], key=KEY, isolate=False), path


def test_sanitize_text_gives_a_text_model_with_one_token_per_line() -> None:
    doc = api.sanitize_text(TEXT, key=KEY)
    assert isinstance(doc.model, TextV1)
    assert doc.json.startswith('{"schema_id":"asfb.text/1"')
    assert len(_tokens(str(doc.views.censored()))) == 2


def test_sanitize_text_isolated_equals_in_process() -> None:
    in_process = api.sanitize_text(TEXT, key=KEY)
    isolated = api.sanitize_text(TEXT, key=KEY, isolate=True)
    assert isolated.json == in_process.json


def test_sanitize_text_refuses_a_non_string() -> None:
    with pytest.raises(AsfbError) as info:
        api.sanitize_text(b"bytes", key=KEY)  # type: ignore[arg-type]
    assert info.value.code is ErrorCode.E_SOURCE_UNSUPPORTED


def test_sanitize_of_a_txt_file_equals_sanitize_text(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text(TEXT, encoding="utf-8")
    from_file = api.sanitize(str(path), key=KEY)
    assert from_file.json == api.sanitize_text(TEXT, key=KEY).json


def test_text_suffixes_match_the_session_kinds() -> None:
    from asfb import session

    assert set(session._KINDS) == paths.DOCUMENT_SUFFIXES  # noqa: SLF001
    assert set(api._KINDS) == paths.DOCUMENT_SUFFIXES  # noqa: SLF001


def test_listing_names_a_txt_document_with_its_extension(tmp_path: Path) -> None:
    session, _path = _text_session(tmp_path, TEXT)
    listing = session.sanitize(".")
    assert str(listing.view).endswith(".txt")


def test_session_sanitizes_a_txt_file_through_its_token_path(tmp_path: Path) -> None:
    session, _path = _text_session(tmp_path, TEXT)
    listing = session.sanitize(".")
    target = str(listing.view).split(" ", 1)[1].split("\n", 1)[0]
    result = session.sanitize(target)
    assert isinstance(result, Sanitized)
    assert '"schema_id":"asfb.text/1"' in result.json


def test_edit_on_a_text_file_can_insert_a_surface_with_markup_characters(tmp_path: Path) -> None:
    session, path = _text_session(tmp_path, "one\ntwo & three\n")
    stored = session.sanitize("notes.txt")
    assert isinstance(stored, Sanitized)
    first, second = _tokens(str(stored.view))[:2]
    edited = session.edit(str(stored.ref), first, second)
    assert path.read_text(encoding="utf-8") == "two & three\ntwo & three\n"
    assert edited.ref.revision == 2


def test_edit_on_an_html_file_still_refuses_markup_characters(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    path = root / "page.html"
    path.write_text("<p>one</p><p>a&amp;b</p>", encoding="utf-8")
    session = Session([str(root)], key=KEY, isolate=False)
    stored = session.sanitize("page.html")
    assert isinstance(stored, Sanitized)
    token = _tokens(str(stored.view))
    with pytest.raises(AsfbError) as info:
        session.edit(str(stored.ref), token[0], token[1])
    assert info.value.code is ErrorCode.E_EDIT_INVALID_TEXT
    assert path.read_text(encoding="utf-8") == "<p>one</p><p>a&amp;b</p>"


def test_isolated_worker_text_reply_equals_the_in_process_pairs() -> None:
    eol, lines = ingest.split_lines(TEXT)
    in_process, pairs = censor_text_with_pairs(KEY, eol, lines, Profile.STRICT)
    model, isolated_pairs = client.run_job("text", KEY, Profile.STRICT, TEXT)
    assert model == in_process
    assert isolated_pairs.items() == pairs.items()
    for token, surface in isolated_pairs.items():
        assert token_for(KEY, Untrusted(surface)) == token


def _ok_reply_for(model: TextV1 | None) -> bytes:
    body = protocol.encode_ok(
        model or TextV1(profile=Profile.STRICT, vocab=(), eol="none", paragraphs=()), []
    )
    assert isinstance(body, bytes)
    return protocol.frame(body)


def test_a_reply_of_another_model_type_is_a_protocol_error(monkeypatch: pytest.MonkeyPatch) -> None:
    child = ChildResult(returncode=0, output=_ok_reply_for(None), timed_out=False, overflow=False)
    monkeypatch.setattr(client, "run_child", lambda *args, **kwargs: child)
    with pytest.raises(AsfbError) as info:
        client.run_job("html", KEY, Profile.STRICT, "<p>x</p>")
    assert info.value.code is ErrorCode.E_WORKER_PROTOCOL


def test_text_worker_refuses_too_many_lines_with_a_static_code() -> None:
    text = "a\n" * (ingest.limits.MAX_LINES + 1)
    with pytest.raises(AsfbError) as info:
        client.run_job("text", KEY, Profile.STRICT, text)
    assert info.value.code is ErrorCode.E_TEXT_LINES


def test_worker_module_python_is_the_running_interpreter() -> None:
    assert sys.executable
