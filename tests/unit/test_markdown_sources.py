"""Markdown as a source: the API and the session dispatch, isolated equality, and edit on .md."""

import re
from pathlib import Path

import pytest

from asfb import api
from asfb.errors import AsfbError, ErrorCode
from asfb.sandbox import client
from asfb.schema.doc import Profile
from asfb.session import Sanitized, Session
from asfb.types.key import SessionKey

KEY = SessionKey(bytes(range(32)))
SOURCE = "# Plan\n\n- [x] done\n\nSee [docs](https://docs.example.org/a).\n"
TOKEN_RE = re.compile(r"t_[0-9a-f]{16}")


def test_sanitize_markdown_isolated_equals_in_process() -> None:
    in_process = api.sanitize_markdown(SOURCE, key=KEY)
    isolated = api.sanitize_markdown(SOURCE, key=KEY, isolate=True)
    assert isolated.json == in_process.json
    assert in_process.json.startswith('{"schema_id":"asfb.md/1"')


@pytest.mark.parametrize("suffix", [".md", ".markdown", ".MD"])
def test_markdown_suffixes_are_sanitized_as_markdown(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"notes{suffix}"
    path.write_text(SOURCE, encoding="utf-8")
    assert api.sanitize(str(path), key=KEY).json == api.sanitize_markdown(SOURCE, key=KEY).json


def test_a_reply_of_another_kind_is_a_protocol_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from asfb.sandbox import protocol
    from asfb.sandbox.client import ChildResult
    from asfb.schema.text import TextV1

    body = protocol.encode_ok(
        TextV1(profile=Profile.STRICT, vocab=(), eol="none", paragraphs=()), []
    )
    assert isinstance(body, bytes)
    child = ChildResult(returncode=0, output=protocol.frame(body), timed_out=False, overflow=False)
    monkeypatch.setattr(client, "run_child", lambda *args, **kwargs: child)
    with pytest.raises(AsfbError) as info:
        client.run_job("md", KEY, Profile.STRICT, SOURCE)
    assert info.value.code is ErrorCode.E_WORKER_PROTOCOL


def test_edit_replaces_a_heading_marker_in_a_markdown_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    path = root / "notes.md"
    path.write_text("# Plan\n", encoding="utf-8")
    session = Session([str(root)], key=KEY, isolate=False)
    stored = session.sanitize("notes.md")
    assert isinstance(stored, Sanitized)
    token = TOKEN_RE.search(str(stored.view))
    assert token is not None
    edited = session.edit(str(stored.ref), f"# {token.group(0)}", f"## {token.group(0)}")
    assert path.read_text(encoding="utf-8") == "## Plan\n"
    assert edited.ref.revision == 2


def test_edit_on_markdown_does_not_apply_the_html_markup_rule(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    path = root / "notes.md"
    path.write_text("one\n\ntwo & three\n", encoding="utf-8")
    session = Session([str(root)], key=KEY, isolate=False)
    stored = session.sanitize("notes.md")
    assert isinstance(stored, Sanitized)
    first, second = TOKEN_RE.findall(str(stored.view))[:2]
    session.edit(str(stored.ref), first, second)
    assert path.read_text(encoding="utf-8") == "two & three\n\ntwo & three\n"
