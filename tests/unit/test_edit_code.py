"""Lexeme edit and lexeme diff of Python sources: matching, tokens, new names, newlines and stale
files, in process and isolated; and the diff units of two Python revisions.

Names in these fixtures are chosen outside the public vocabularies (path, load and name are public
stdlib terms, so they are never tokens), otherwise the tests would not exercise a token.
"""

import json
from pathlib import Path

import pytest

from asfb.egress import code_units
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.tokens import token_for
from asfb.session import Diffed, Sanitized, Session
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted

KEY = SessionKey(bytes(range(32)))
SOURCE = "def ingest(payload):\n    return payload\n\n\nVALUE = 'secret'\n"
UNKNOWN = "t_0123456789abcdef"


def _token(surface: str) -> str:
    """The token the session mints for a surface, under the same key."""
    return str(token_for(KEY, Untrusted(surface)))


def _session(root: Path, *, isolate: bool = False) -> Session:
    return Session([str(root)], key=KEY, isolate=isolate)


def _stored(session: Session, name: str) -> Sanitized:
    result = session.sanitize(name)
    assert isinstance(result, Sanitized)
    return result


def _code(session: Session, ref: str, old: str, new: str) -> ErrorCode:
    with pytest.raises(AsfbError) as info:
        session.edit(ref, old, new)
    return info.value.code


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "mod.py").write_text(SOURCE, encoding="utf-8")
    return root


def test_an_invented_token_in_new_is_refused_as_unknown(project: Path) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    old = f"return {_token('payload')}"
    assert _code(session, str(stored.ref), old, f"return {UNKNOWN}") is (
        ErrorCode.E_EDIT_UNKNOWN_TOKEN
    )
    assert (project / "mod.py").read_text(encoding="utf-8") == SOURCE


def test_an_invented_token_in_old_is_refused_as_unknown(project: Path) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    assert _code(session, str(stored.ref), f"return {UNKNOWN}", "return 1") is (
        ErrorCode.E_EDIT_UNKNOWN_TOKEN
    )


def test_a_known_token_is_replaced_by_its_surface_and_the_file_is_censored_again(
    project: Path,
) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    edited = session.edit(str(stored.ref), f"return {_token('payload')}", "return 0")
    assert (
        (project / "mod.py")
        .read_text(encoding="utf-8")
        .endswith("return 0\n\n\nVALUE = 'secret'\n")
    )
    assert edited.ref.revision == stored.ref.revision + 1
    assert "return 0" in edited.view  # 0 is a public number, shown as itself


def test_layout_of_the_agent_text_does_not_have_to_match_the_file(project: Path) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    old = f"def ingest ( {_token('payload')} ) :"
    session.edit(str(stored.ref), old, "def ingest(payload, extra):")
    assert "def ingest(payload, extra):" in (project / "mod.py").read_text(encoding="utf-8")


def test_a_new_identifier_is_allowed_and_marked_as_the_model_origin(project: Path) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    session.edit(str(stored.ref), f"return {_token('payload')}", "return fresh_name")
    assert "return fresh_name" in (project / "mod.py").read_text(encoding="utf-8")
    assert session._vault.model_origin(Token(_token("fresh_name")))  # noqa: SLF001


def test_a_new_identifier_longer_than_the_limit_is_refused(project: Path) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    old = f"return {_token('payload')}"
    assert _code(session, str(stored.ref), old, "return " + "n" * 33) is (
        ErrorCode.E_EDIT_INVALID_TEXT
    )
    assert (project / "mod.py").read_text(encoding="utf-8") == SOURCE


def test_non_ascii_text_is_refused(project: Path) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    assert _code(session, str(stored.ref), "return", "return é") is (ErrorCode.E_EDIT_INVALID_TEXT)


def test_no_match_and_ambiguous_match_are_refused(tmp_path: Path) -> None:
    (tmp_path / "dup.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    session = _session(tmp_path)
    dup = _stored(session, "dup.py")
    assert _code(session, str(dup.ref), "x = 2", "x = 3") is ErrorCode.E_EDIT_NO_MATCH
    assert _code(session, str(dup.ref), "x = 1", "x = 3") is ErrorCode.E_EDIT_AMBIGUOUS
    assert (tmp_path / "dup.py").read_text(encoding="utf-8") == "x = 1\nx = 1\n"


def test_a_stale_file_is_refused(project: Path) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    (project / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert _code(session, str(stored.ref), "VALUE", "OTHER") is ErrorCode.E_EDIT_STALE


def test_an_edit_that_breaks_the_tokenizer_leaves_the_file_intact(project: Path) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    assert _code(session, str(stored.ref), "return", "return '") is (
        ErrorCode.E_CODE_TOKENIZE_FAILED
    )
    assert (project / "mod.py").read_text(encoding="utf-8") == SOURCE


def test_crlf_file_keeps_its_line_endings(tmp_path: Path) -> None:
    root = tmp_path / "crlf"
    root.mkdir()
    (root / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    session = _session(root)
    stored = _stored(session, "a.py")
    session.edit(str(stored.ref), "y = 2", "y = 3")
    assert (root / "a.py").read_bytes() == b"x = 1\r\ny = 3\r\n"


def test_isolated_and_in_process_edits_agree(tmp_path: Path) -> None:
    results: list[tuple[str, str]] = []
    for isolate in (False, True):
        root = tmp_path / f"iso-{isolate}"
        root.mkdir()
        (root / "mod.py").write_text(SOURCE, encoding="utf-8")
        session = _session(root, isolate=isolate)
        stored = _stored(session, "mod.py")
        edited = session.edit(str(stored.ref), "return", "return None")
        results.append((edited.json, (root / "mod.py").read_text(encoding="utf-8")))
    assert results[0] == results[1]


@pytest.fixture
def two_revisions(project: Path) -> tuple[Session, str, str]:
    session = _session(project)
    first = _stored(session, "mod.py")
    second = session.edit(str(first.ref), f"return {_token('payload')}", "return None")
    return session, str(first.ref), str(second.ref)


def test_diff_of_two_python_revisions_uses_lexeme_units(
    two_revisions: tuple[Session, str, str],
) -> None:
    session, before, after = two_revisions
    diffed: Diffed = session.diff(before, after)
    model = json.loads(diffed.json)
    assert model["units"] == "lexeme"
    assert len(model["hunks"]) == 1
    body = str(diffed.view).split("\n")[2:]
    assert [line for line in body if line.startswith("-")]
    assert [line for line in body if line.startswith("+") and "None" in line]


def test_a_revision_compared_with_itself_has_no_lexeme_hunks(
    two_revisions: tuple[Session, str, str],
) -> None:
    session, before, _after = two_revisions
    model = json.loads(session.diff(before, before).json)
    assert model == {
        **model,
        "hunks": [],
        "units": "lexeme",
    }


def test_diff_between_a_python_source_and_html_is_line_based(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text("<p>x</p>", encoding="utf-8")
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    session = _session(tmp_path)
    page = _stored(session, "page.html")
    module = _stored(session, "mod.py")
    assert json.loads(session.diff(str(page.ref), str(module.ref)).json)["units"] == "line"


def test_code_units_refuse_a_non_code_document(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text("<p>x</p>", encoding="utf-8")
    session = _session(tmp_path)
    page = _stored(session, "page.html")
    with pytest.raises(AsfbError):
        code_units(page.json)


def test_code_units_are_one_text_per_lexeme(project: Path) -> None:
    session = _session(project)
    stored = _stored(session, "mod.py")
    units = code_units(stored.json)
    assert len(units) == len(json.loads(stored.json)["lexemes"])
    assert "def" in units
    assert "NEWLINE" in units


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("aé", ErrorCode.E_EDIT_INVALID_TEXT),
        ("a\x00b", ErrorCode.E_EDIT_INVALID_TEXT),
        ("a\x0cb", ErrorCode.E_EDIT_INVALID_TEXT),
        ("a" * 65_537, ErrorCode.E_EDIT_TOO_LARGE),
        (None, ErrorCode.E_EDIT_INVALID_TEXT),
    ],
)
def test_check_code_text_refuses_what_an_edit_cannot_carry(text: object, code: ErrorCode) -> None:
    from asfb.editing import check_code_text

    assert check_code_text(text) is code


def test_check_code_text_accepts_printable_ascii_tab_and_newlines() -> None:
    from asfb.editing import check_code_text

    assert check_code_text("def t_0123456789abcdef(x):\n\treturn 0\r\n") is None
