"""Corpus files censored with a fixed key; each view must equal its golden after renumbering."""

from pathlib import Path

import pytest

from asfb import sanitize
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from tests.canary.harness import renumber

CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus" / "html"
GOLDEN_DIR = CORPUS_DIR / "golden"
PROPERTY_ONLY = frozenset({"malformed", "comments_cdata"})
KEY = SessionKey(bytes(range(32)))
FILES = sorted(path.stem for path in CORPUS_DIR.glob("*.html"))
GOLDENS = [name for name in FILES if name not in PROPERTY_ONLY]


def test_corpus_has_the_specified_files() -> None:
    expected = {
        "readme_example",
        "attributes",
        "urls",
        "scripts_styles",
        "comments_cdata",
        "entities",
        "malformed",
        "custom_elements",
        "bidi_zero_width",
        "forms",
        "head_meta",
        "bootstrap",
    }
    assert set(FILES) == expected


def test_goldens_exist_for_every_file_except_the_property_only_ones() -> None:
    assert {path.stem for path in GOLDEN_DIR.glob("*.txt")} == set(GOLDENS)


@pytest.mark.parametrize("name", FILES)
def test_corpus_file_is_plain_utf8_with_lf_endings(name: str) -> None:
    data = (CORPUS_DIR / f"{name}.html").read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in data
    data.decode("utf-8")


def test_bidi_file_carries_real_zero_width_code_points() -> None:
    text = (CORPUS_DIR / "bidi_zero_width.html").read_text(encoding="utf-8")
    assert "​" in text
    assert "‮" in text
    assert "\\u" not in text


@pytest.mark.parametrize("name", FILES)
def test_every_corpus_file_gives_a_doc(name: str) -> None:
    doc = sanitize(CORPUS_DIR / f"{name}.html", key=KEY)
    assert doc.profile is Profile.STRICT


@pytest.mark.parametrize("name", GOLDENS)
def test_view_matches_golden(name: str) -> None:
    doc = sanitize(CORPUS_DIR / f"{name}.html", key=KEY)
    expected = (GOLDEN_DIR / f"{name}.txt").read_bytes().decode("utf-8")
    assert b"\r" not in (GOLDEN_DIR / f"{name}.txt").read_bytes()
    assert renumber(doc.views.censored()) + "\n" == expected
