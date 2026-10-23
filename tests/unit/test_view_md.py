"""The markdown view: each construct prints in its own syntax, and the six corpus files match
their golden views after token renumbering."""

from pathlib import Path

import pytest

from asfb.egress import emit, render
from asfb.ingest.markdown import parse_markdown
from asfb.sanitize.markdown import censor_md_with_pairs
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.vocab import md_registry
from tests.canary.harness import renumber

KEY = SessionKey(bytes(range(32)))
CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "md"
FILES = sorted(CORPUS.glob("*.md"))


def _view(source: str) -> str:
    model, _pairs = censor_md_with_pairs(KEY, parse_markdown(source), Profile.STRICT)
    return str(render(emit(model, vocab=md_registry())))


@pytest.mark.parametrize("path", FILES, ids=[path.stem for path in FILES])
def test_corpus_view_matches_its_golden(path: Path) -> None:
    view = renumber(_view(path.read_text(encoding="utf-8")))
    golden = (CORPUS / "golden" / f"{path.stem}.txt").read_text(encoding="utf-8")
    assert view + "\n" == golden


def test_corpus_has_six_documents_and_goldens() -> None:
    assert [path.stem for path in FILES] == [
        "code",
        "headings",
        "links",
        "lists",
        "quotes",
        "tables",
    ]
    assert len(list((CORPUS / "golden").glob("*.txt"))) == 6


@pytest.mark.parametrize(
    ("source", "expected_prefix"),
    [
        ("# h\n", "# "),
        ("## h\n", "## "),
        ("- a\n", "- "),
        ("1. a\n", "1. "),
        ("- [x] a\n", "- [x] "),
        ("- [ ] a\n", "- [ ] "),
        ("> q\n", "> "),
        ("---\n", "---"),
        ("```python\nx\n```\n", "```python"),
        ("~~~\nx\n~~~\n", "~~~"),
        ("    code\n", "    "),
        ("[l]: /x\n", "[t_"),
    ],
)
def test_each_construct_prints_in_its_syntax(source: str, expected_prefix: str) -> None:
    assert _view(source).startswith(expected_prefix)


def test_blocks_are_separated_by_one_empty_line() -> None:
    lines = _view("# a\n\nb\n").split("\n")
    assert lines[1] == ""
    assert len(lines) == 3


def test_table_delimiters_follow_alignment() -> None:
    view = _view("| a | b | c | d |\n|:-|:-:|-:|-|\n| 1 | 2 | 3 | 4 |\n")
    assert "| :-- | :-: | --: | --- |" in view


def test_image_and_link_render_their_title() -> None:
    view = _view('[a](/x "T") ![b](/y)\n')
    assert ' "t_' in view
    assert "![t_" in view
