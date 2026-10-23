"""Markdown censoring: each construct is visible, its text is one token, public words stay public,
and every pair recomputes its token. Pairs are checked through the model, never printed."""

import re

import pytest

from asfb.egress import emit, render
from asfb.ingest.markdown import parse_markdown
from asfb.sanitize.markdown import censor_md_with_pairs
from asfb.sanitize.tokens import token_for
from asfb.schema import md as schema
from asfb.schema.doc import Profile
from asfb.schema.html import Url
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted
from asfb.types.values import EnumValue
from asfb.vocab import md_registry

KEY = SessionKey(bytes(range(32)))
TOKEN_RE = re.compile(r"t_[0-9a-f]{16}")


def _censor(source: str) -> tuple[schema.MdV1, tuple[tuple[Token, str], ...]]:
    model, pairs = censor_md_with_pairs(KEY, parse_markdown(source), Profile.STRICT)
    return model, pairs.items()


def _view(source: str) -> str:
    model, _pairs = _censor(source)
    return str(render(emit(model, vocab=md_registry())))


def _block(source: str) -> schema.Block:
    model, _pairs = _censor(source)
    (block,) = model.blocks
    return block


def test_heading_inlines_and_level() -> None:
    block = _block("## Plan\n")
    assert isinstance(block, schema.Heading)
    assert block.level == 2
    assert isinstance(block.inlines[0], schema.MdText)


def test_paragraph_keeps_one_entry_per_line() -> None:
    block = _block("one\ntwo\n")
    assert isinstance(block, schema.Paragraph)
    assert len(block.lines) == 2


def test_code_span_is_one_token_and_drops_one_space_at_each_end() -> None:
    block = _block("`  x  `\n")
    assert isinstance(block, schema.Paragraph)
    (inline,) = block.lines[0]
    assert isinstance(inline, schema.MdCode)
    # One space is dropped at each end: " x " normalizes to "x".
    assert inline.text == token_for(KEY, Untrusted("x"))


def test_unmatched_backticks_are_text() -> None:
    block = _block("a ``` b\n")
    assert isinstance(block, schema.Paragraph)
    assert all(isinstance(inline, schema.MdText) for inline in block.lines[0])


def test_code_span_with_only_spaces_is_text() -> None:
    block = _block("`   `\n")
    assert isinstance(block, schema.Paragraph)
    assert all(isinstance(inline, schema.MdText) for inline in block.lines[0])


def test_link_has_text_url_and_title() -> None:
    block = _block('[a](https://h.example/p?q=1#f "T")\n')
    assert isinstance(block, schema.Paragraph)
    (link,) = block.lines[0]
    assert isinstance(link, schema.MdLink)
    assert link.text is not None
    assert isinstance(link.url, Url)
    assert link.title is not None


def test_image_has_alt_url_and_single_quoted_title() -> None:
    block = _block("![alt](/x.png 'T')\n")
    assert isinstance(block, schema.Paragraph)
    (image,) = block.lines[0]
    assert isinstance(image, schema.MdImage)
    assert image.alt is not None
    assert image.title is not None


def test_link_with_empty_destination_has_no_url() -> None:
    block = _block("[a]()\n")
    assert isinstance(block, schema.Paragraph)
    (link,) = block.lines[0]
    assert isinstance(link, schema.MdLink)
    assert link.url is None


def test_autolink_needs_a_scheme_and_is_url_shaped() -> None:
    # A line that starts with "<" is an HTML block, so the autolink sits after some text.
    block = _block("see <https://a.example/x>\n")
    assert isinstance(block, schema.Paragraph)
    assert any(isinstance(inline, schema.MdAutolink) for inline in block.lines[0])
    block = _block("see <not a link>\n")
    assert isinstance(block, schema.Paragraph)
    assert all(isinstance(inline, schema.MdText) for inline in block.lines[0])


def test_escaped_punctuation_keeps_both_characters_in_the_surface() -> None:
    _model, pairs = _censor("\\*star\\*\n")
    assert any(surface == "\\*star\\*" for _token, surface in pairs)


def test_public_language_is_an_enum_value_and_case_folds() -> None:
    block = _block("```Python\nx\n```\n")
    assert isinstance(block, schema.CodeBlock)
    assert block.lang == EnumValue(vocab="md.languages", term="python")


def test_private_language_is_a_token() -> None:
    block = _block("```secretlang\nx\n```\n")
    assert isinstance(block, schema.CodeBlock)
    assert isinstance(block.lang, Token)


def test_code_block_text_is_one_token() -> None:
    block = _block("```\nline one\nline two\n```\n")
    assert isinstance(block, schema.CodeBlock)
    assert block.text is not None


def test_empty_code_block_has_no_text() -> None:
    block = _block("```\n```\n")
    assert isinstance(block, schema.CodeBlock)
    assert block.text is None


def test_table_cells_are_inline_lists() -> None:
    block = _block("| a | `b` |\n| - | - |\n| 1 | 2 |\n")
    assert isinstance(block, schema.Table)
    assert len(block.header) == 2
    assert isinstance(block.header[1][0], schema.MdCode)


def test_task_marks_and_depth() -> None:
    model, _pairs = _censor("- [x] done\n  - [ ] nested\n")
    first, second = model.blocks
    assert isinstance(first, schema.Item)
    assert isinstance(second, schema.Item)
    assert (first.task, second.task, second.depth) == ("done", "open", 1)


def test_html_block_is_one_token() -> None:
    block = _block("<div>\nsome text\n")
    assert isinstance(block, schema.HtmlBlock)


def test_front_matter_is_one_token_or_none() -> None:
    model, _pairs = _censor("---\n---\n")
    (block,) = model.blocks
    assert isinstance(block, schema.FrontMatter)
    assert block.text is None


def test_link_definition_with_title() -> None:
    block = _block('[ref]: https://x.example "Title"\n')
    assert isinstance(block, schema.LinkDef)
    assert block.title is not None
    assert block.url is not None


def test_a_line_with_too_many_inlines_becomes_one_text() -> None:
    line = "`a` " * (schema.MAX_INLINES + 1)
    block = _block(line + "\n")
    assert isinstance(block, schema.Paragraph)
    (only,) = block.lines[0]
    assert isinstance(only, schema.MdText)


def test_every_pair_recomputes_its_token() -> None:
    source = (
        "# Head\n\n- [x] item `code`\n\n| a | b |\n| - | - |\n| c | d |\n\n"
        "[l](https://h.example/p) ![i](/i.png)\n\n```python\nx\n```\n"
    )
    _model, pairs = _censor(source)
    for token, surface in pairs:
        assert token_for(KEY, Untrusted(surface)) == token


@pytest.mark.parametrize(
    "source",
    ["# Head\n", "- item\n", "> quote\n", "```\ncode\n```\n", "[a](https://x.example)\n"],
)
def test_every_token_of_the_model_has_a_pair(source: str) -> None:
    model, pairs = _censor(source)
    minted = {token for token, _surface in pairs}
    for token in TOKEN_RE.findall(_view(source)):
        assert token in minted
    assert model.blocks
