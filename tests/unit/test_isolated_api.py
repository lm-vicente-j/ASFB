"""sanitize(..., isolate=True) gives the same Doc as the in-process path for the same key.

Each isolated call starts one worker (about 1.5 s; most of it is importing asfb). The corpus check
therefore runs once per file. Error paths that need no worker are checked without spawning.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from asfb import api
from asfb.errors import AsfbError, ErrorCode
from asfb.types.key import SessionKey

CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus" / "html"
FILES = sorted(path.stem for path in CORPUS_DIR.glob("*.html"))
KEY = SessionKey(bytes(range(32)))
CANARY = "CANARY-ISO-2208"


def _outcome(call: Callable[[], api.Doc]) -> tuple[str, str, str] | ErrorCode:
    """(emitted JSON, censored view, profile) for a Doc, or the static code of the error."""
    try:
        doc = call()
    except AsfbError as error:
        return error.code
    return doc.json, str(doc.views.censored()), doc.profile.value


@pytest.mark.parametrize("name", FILES)
def test_isolated_corpus_file_equals_in_process(name: str) -> None:
    html = (CORPUS_DIR / f"{name}.html").read_text(encoding="utf-8")
    in_process = _outcome(lambda: api.sanitize_html(html, key=KEY))
    isolated = _outcome(lambda: api.sanitize_html(html, key=KEY, isolate=True))
    assert isolated == in_process


def test_isolated_path_file_equals_in_process_path(tmp_path: Path) -> None:
    page = tmp_path / "page.htm"
    page.write_text(
        (CORPUS_DIR / "readme_example.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    in_process = api.sanitize(page, key=KEY)
    isolated = api.sanitize(page, key=KEY, isolate=True)
    assert isolated.json == in_process.json
    assert str(isolated.views.censored()) == str(in_process.views.censored())
    assert isolated.model == in_process.model


def test_isolated_doc_has_the_same_shape_as_the_in_process_doc() -> None:
    html = "<p>Contact ana@example.com</p>"
    in_process = api.sanitize_html(html, key=KEY)
    isolated = api.sanitize_html(html, key=KEY, isolate=True)
    assert isolated.profile is in_process.profile
    assert isolated.model == in_process.model
    assert isolated.json == in_process.json


def test_isolated_error_carries_only_the_static_code() -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html(
            f"<!DOCTYPE html><p>{CANARY}</p><!DOCTYPE {CANARY}>", key=KEY, isolate=True
        )
    error = raised.value
    assert error.code is ErrorCode.E_HTML_PARSE
    assert error.args == ("E_HTML_PARSE",)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert CANARY not in repr(error)


def test_isolated_refuses_diffable_without_a_key_before_spawning() -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html("<p>x</p>", profile="diffable", isolate=True)
    assert raised.value.code is ErrorCode.E_PROFILE_KEY


def test_isolated_refuses_an_unknown_profile_before_spawning() -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html("<p>x</p>", profile="lenient", isolate=True)  # type: ignore[arg-type]
    assert raised.value.code is ErrorCode.E_PROFILE


def test_isolated_refuses_a_non_html_path_before_spawning(tmp_path: Path) -> None:
    path = tmp_path / "notes.pdf"
    path.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(AsfbError) as raised:
        api.sanitize(path, key=KEY, isolate=True)
    assert raised.value.code is ErrorCode.E_SOURCE_UNSUPPORTED


def test_isolated_missing_file_gives_the_read_error(tmp_path: Path) -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize(tmp_path / "missing.html", key=KEY, isolate=True)
    assert raised.value.code is ErrorCode.E_SOURCE_READ


def test_isolate_defaults_to_false() -> None:
    assert api.sanitize_html.__kwdefaults__ is not None
    assert api.sanitize_html.__kwdefaults__["isolate"] is False
    assert api.sanitize.__kwdefaults__ is not None
    assert api.sanitize.__kwdefaults__["isolate"] is False
