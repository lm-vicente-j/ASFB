"""Public API: inputs, profiles, keys, Doc contents and clean errors."""

import dataclasses
import pickle
from pathlib import Path

import pytest

import asfb
from asfb import api
from asfb.errors import AsfbError, ErrorCode
from asfb.schema.doc import DocV1, Profile
from asfb.types.key import SessionKey

SAMPLE = (
    '<!DOCTYPE html><html><body class="container"><form action="/invoices/42/send" '
    'method="post"><input type="email" name="customer_email" value="ana@example.com">'
    '<button class="btn btn-primary">Send invoice</button></form>'
    '<script>fetch("/api/x")</script></body></html>'
)
CANARY = "CANARY-ZQX-7731"
KEY = SessionKey(bytes(range(32)))
OTHER_KEY = SessionKey(bytes(range(1, 33)))


def _html(doc: api.Doc) -> DocV1:
    assert isinstance(doc.model, DocV1)
    return doc.model


def _code(error: AsfbError) -> ErrorCode:
    return error.code


def test_all_lists_exactly_the_public_names() -> None:
    expected = {
        "sanitize",
        "sanitize_html",
        "sanitize_text",
        "sanitize_markdown",
        "sanitize_python",
        "Doc",
        "Session",
        "SessionKey",
        "AsfbError",
        "ErrorCode",
    }
    assert set(asfb.__all__) == expected
    assert len(asfb.__all__) == len(expected)
    for name in asfb.__all__:
        assert getattr(asfb, name) is not None


def test_sanitize_html_returns_a_doc_with_all_parts() -> None:
    doc = api.sanitize_html(SAMPLE, key=KEY)
    assert isinstance(doc, api.Doc)
    assert doc.profile is Profile.STRICT
    assert _html(doc).body
    assert doc.views.censored().startswith("<!DOCTYPE html>\n<html>")


def test_sanitize_accepts_a_path(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(SAMPLE, encoding="utf-8")
    from_path = api.sanitize(page, key=KEY)
    from_text = api.sanitize_html(SAMPLE, key=KEY)
    assert from_path.json == from_text.json


def test_sanitize_accepts_a_string_path_with_htm_suffix(tmp_path: Path) -> None:
    page = tmp_path / "page.htm"
    page.write_text("<p>x</p>", encoding="utf-8")
    assert _html(api.sanitize(str(page), key=KEY)).body


@pytest.mark.parametrize("name", ["notes.pdf", "page.html.pdf", "page"])
def test_non_document_path_is_refused(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(AsfbError) as raised:
        api.sanitize(path, key=KEY)
    assert _code(raised.value) is ErrorCode.E_SOURCE_UNSUPPORTED


def test_missing_html_file_gives_read_error(tmp_path: Path) -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize(tmp_path / "missing.html", key=KEY)
    assert _code(raised.value) is ErrorCode.E_SOURCE_READ


def test_diffable_without_key_is_refused() -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html(SAMPLE, profile="diffable")
    assert _code(raised.value) is ErrorCode.E_PROFILE_KEY


def test_diffable_with_key_is_accepted() -> None:
    doc = api.sanitize_html(SAMPLE, profile="diffable", key=KEY)
    assert doc.profile is Profile.DIFFABLE
    assert doc.model.profile is Profile.DIFFABLE


def test_profile_enum_member_is_accepted() -> None:
    doc = api.sanitize_html(SAMPLE, profile=Profile.DIFFABLE, key=KEY)  # type: ignore[arg-type]
    assert doc.profile is Profile.DIFFABLE


@pytest.mark.parametrize("profile", ["Strict", "", "lenient"])
def test_unknown_profile_is_refused(profile: str) -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html(SAMPLE, profile=profile, key=KEY)  # type: ignore[arg-type]
    assert _code(raised.value) is ErrorCode.E_PROFILE


def test_non_session_key_is_refused() -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html(SAMPLE, key=b"x" * 32)  # type: ignore[arg-type]
    assert _code(raised.value) is ErrorCode.E_PROFILE_KEY


def test_same_key_gives_equal_tokens_across_calls() -> None:
    assert api.sanitize_html(SAMPLE, key=KEY).json == api.sanitize_html(SAMPLE, key=KEY).json


def test_different_key_gives_different_tokens() -> None:
    first = api.sanitize_html(SAMPLE, key=KEY).json
    second = api.sanitize_html(SAMPLE, key=OTHER_KEY).json
    assert first != second


def test_no_key_creates_a_fresh_key_per_call() -> None:
    assert api.sanitize_html(SAMPLE).json != api.sanitize_html(SAMPLE).json


def test_doc_is_frozen() -> None:
    doc = api.sanitize_html("<p>x</p>", key=KEY)
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.json = doc.json  # type: ignore[misc]


def test_doc_exposes_no_original_text_or_key() -> None:
    doc = api.sanitize_html(f'<p title="{CANARY}">{CANARY}</p>', key=KEY)
    assert CANARY not in repr(doc)
    assert CANARY not in repr(doc.views)
    assert CANARY not in str(doc.views.censored())
    assert CANARY not in doc.json
    fields = [getattr(doc, field.name) for field in dataclasses.fields(doc)]
    assert all(not isinstance(value, SessionKey) for value in fields)
    assert CANARY.encode() not in pickle.dumps(doc)


def test_views_hold_censored_text_only() -> None:
    assert CANARY not in api.sanitize_html(f"<p>{CANARY}</p>", key=KEY).views.censored()


def test_errors_carry_only_a_static_code() -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html(f"<!DOCTYPE html><p>{CANARY}</p><!DOCTYPE {CANARY}>", key=KEY)
    error = raised.value
    assert _code(error) is ErrorCode.E_HTML_PARSE
    assert str(error) == "E_HTML_PARSE"
    assert CANARY not in repr(error)
    assert error.args == ("E_HTML_PARSE",)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_unexpected_exception_becomes_internal_without_chaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: object) -> None:
        raise RuntimeError(CANARY)

    monkeypatch.setattr(api, "censor_document", broken)
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html(SAMPLE, key=KEY)
    error = raised.value
    assert _code(error) is ErrorCode.E_INTERNAL
    assert CANARY not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_duplicate_doctype_is_refused_as_html_parse() -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html("<!DOCTYPE html><!DOCTYPE html>", key=KEY)
    assert _code(raised.value) is ErrorCode.E_HTML_PARSE
