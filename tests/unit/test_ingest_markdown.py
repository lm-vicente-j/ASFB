"""Markdown block scanner: one positive and one boundary case per rule, limits, and the Untrusted
wrapping of every block text. Pure in-process parsing; nothing here reaches output."""

import time

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import markdown
from asfb.ingest.limits import MAX_LIST_DEPTH, MAX_QUOTE
from asfb.ingest.markdown import (
    RawCode,
    RawFrontMatter,
    RawHeading,
    RawHtml,
    RawItem,
    RawLinkDef,
    RawParagraph,
    RawRule,
    RawTable,
    parse_markdown,
)
from asfb.types.untrusted import Untrusted, unwrap


def _text(value: Untrusted[str] | None) -> str | None:
    return None if value is None else unwrap(value)


def test_atx_headings_by_level_and_closing_hashes() -> None:
    blocks = parse_markdown("# One\n## Two ##\n###### Six #\n#seven\n####### seven\n")
    # "#seven" and "####### seven" are not headings: they form one paragraph.
    assert [type(block) for block in blocks] == [RawHeading, RawHeading, RawHeading, RawParagraph]
    headings = [block for block in blocks if isinstance(block, RawHeading)]
    assert [(block.level, _text(block.text)) for block in headings] == [
        (1, "One"),
        (2, "Two"),
        (6, "Six"),
    ]


def test_empty_atx_heading_has_empty_text() -> None:
    (block,) = parse_markdown("#\n")
    assert isinstance(block, RawHeading)
    assert _text(block.text) == ""


def test_four_leading_spaces_are_not_an_atx_heading() -> None:
    (block,) = parse_markdown("    # not a heading\n")
    assert isinstance(block, RawCode)


def test_setext_headings_take_the_paragraph_text_joined_by_spaces() -> None:
    (block,) = parse_markdown("First\n  second  \n===\n")
    assert isinstance(block, RawHeading)
    assert (block.level, _text(block.text)) == (1, "First second")
    (block,) = parse_markdown("Sub\n---\n")
    assert isinstance(block, RawHeading)
    assert block.level == 2


def test_a_rule_without_a_paragraph_is_not_a_setext_heading() -> None:
    (block,) = parse_markdown("\n---\n")
    assert isinstance(block, RawRule)


@pytest.mark.parametrize("line", ["---", "* * *", "___", "- - - -"])
def test_rules_of_three_or_more_marks(line: str) -> None:
    (block,) = parse_markdown(f"{line}\n")
    assert isinstance(block, RawRule)


def test_two_dashes_are_not_a_rule() -> None:
    (block,) = parse_markdown("--\n")
    assert isinstance(block, RawParagraph)


def test_bullet_and_ordered_items_with_depth_from_indentation() -> None:
    blocks = parse_markdown("- a\n  - b\n1. c\n2) d\n")
    items = [block for block in blocks if isinstance(block, RawItem)]
    assert [(item.depth, item.ordered) for item in items] == [
        (0, False),
        (1, False),
        (0, True),
        (0, True),
    ]


def test_nested_items_depth_is_capped() -> None:
    (block,) = parse_markdown(" " * 200 + "- deep\n")
    assert isinstance(block, RawItem)
    assert block.depth == MAX_LIST_DEPTH


@pytest.mark.parametrize(
    ("line", "task", "text"),
    [
        ("- [ ] open", "open", "open"),
        ("- [x] done", "done", "done"),
        ("- [X] also done", "done", "also done"),
        ("- [y] not a task", "none", "[y] not a task"),
        ("- [ ]", "none", "[ ]"),
    ],
)
def test_task_marks(line: str, task: str, text: str) -> None:
    (block,) = parse_markdown(line + "\n")
    assert isinstance(block, RawItem)
    assert (block.task, _text(block.text)) == (task, text)


def test_a_marker_without_a_space_is_not_an_item() -> None:
    (block,) = parse_markdown("-x\n")
    assert isinstance(block, RawParagraph)


def test_backtick_fence_with_info_and_closing_fence() -> None:
    (block,) = parse_markdown("```python extra\nprint(1)\n```\n")
    assert isinstance(block, RawCode)
    assert block.fence == "backtick"
    assert _text(block.info) == "python"
    assert _text(block.text) == "print(1)"


def test_tilde_fence_needs_a_closing_run_at_least_as_long() -> None:
    (block,) = parse_markdown("~~~~\na\n~~~\nb\n~~~~\n")
    assert isinstance(block, RawCode)
    assert block.fence == "tilde"
    assert _text(block.text) == "a\n~~~\nb"


def test_a_backtick_in_the_info_string_is_not_a_fence() -> None:
    (block,) = parse_markdown("``` a`b\n")
    assert isinstance(block, RawParagraph)


def test_unclosed_fence_runs_to_the_end() -> None:
    (block,) = parse_markdown("```\nx\ny\n")
    assert isinstance(block, RawCode)
    assert _text(block.text) == "x\ny"
    assert block.info is None


def test_indented_code_after_a_blank_line_and_not_after_an_item() -> None:
    blocks = parse_markdown("text\n\n    code\n      more\n")
    assert isinstance(blocks[-1], RawCode)
    assert blocks[-1].fence == "indent"
    assert _text(blocks[-1].text) == "code\n  more"


def test_indented_line_after_an_item_is_not_code() -> None:
    blocks = parse_markdown("- a\n\n     more\n")
    assert not any(isinstance(block, RawCode) for block in blocks)


def test_block_quotes_nest_and_count_depth() -> None:
    blocks = parse_markdown("> one\n> > two\n>> three\n")
    paragraphs = [block for block in blocks if isinstance(block, RawParagraph)]
    assert [block.quote for block in paragraphs] == [1, 2]
    assert [_text(line) for line in paragraphs[1].lines] == ["two", "three"]


def test_block_quote_depth_limit() -> None:
    assert parse_markdown(">" * MAX_QUOTE + " ok\n")
    with pytest.raises(AsfbError) as info:
        parse_markdown(">" * (MAX_QUOTE + 1) + " too deep\n")
    assert info.value.code is ErrorCode.E_MD_DEPTH


def test_table_with_alignment_and_rows() -> None:
    (block,) = parse_markdown("| a | b |\n|:--|--:|\n| 1 | 2 |\n| 3 |\n")
    assert isinstance(block, RawTable)
    assert block.align == ("left", "right")
    assert [_text(cell) for cell in block.header] == ["a", "b"]
    assert [[_text(cell) for cell in row] for row in block.rows] == [["1", "2"], ["3", ""]]


def test_table_centre_and_none_alignment() -> None:
    (block,) = parse_markdown("a | b\n:-: | ---\n")
    assert isinstance(block, RawTable)
    assert block.align == ("center", "none")


def test_table_with_uneven_delimiter_is_a_paragraph() -> None:
    (block,) = parse_markdown("a | b\n:-: | --- | ---\n")
    assert isinstance(block, RawParagraph)


def test_table_row_without_a_pipe_ends_the_table() -> None:
    blocks = parse_markdown("a | b\n-|-\n1 | 2\nplain\n")
    assert isinstance(blocks[0], RawTable)
    assert isinstance(blocks[1], RawParagraph)


def test_escaped_pipe_does_not_split_a_cell() -> None:
    (block,) = parse_markdown("a | b\n- | -\nx \\| y | z\n")
    assert isinstance(block, RawTable)
    assert [_text(cell) for cell in block.rows[0]] == ["x \\| y", "z"]


def test_html_block_runs_until_a_blank_line() -> None:
    blocks = parse_markdown("<div>\nhi\n\nafter\n")
    assert isinstance(blocks[0], RawHtml)
    assert _text(blocks[0].text) == "<div>\nhi"
    assert isinstance(blocks[1], RawParagraph)


def test_link_definition_with_destination_and_title() -> None:
    (block,) = parse_markdown('[Label]: <http://x.example/a b> "Title"\n')
    assert isinstance(block, RawLinkDef)
    assert _text(block.label) == "Label"
    assert _text(block.dest) == "http://x.example/a b"
    assert _text(block.title) == "Title"


def test_link_definition_without_title() -> None:
    (block,) = parse_markdown("[a]: /path\n")
    assert isinstance(block, RawLinkDef)
    assert block.title is None


def test_front_matter_closes_on_a_triple_dash_or_triple_dot() -> None:
    blocks = parse_markdown("---\ntitle: x\n...\n# Head\n")
    assert isinstance(blocks[0], RawFrontMatter)
    assert _text(blocks[0].text) == "title: x"
    assert isinstance(blocks[1], RawHeading)


def test_front_matter_without_a_close_is_a_rule_and_the_scan_goes_on() -> None:
    blocks = parse_markdown("---\ntext\n")
    assert isinstance(blocks[0], RawRule)
    assert not any(isinstance(block, RawFrontMatter) for block in blocks)


def test_paragraph_lines_are_kept_separately_and_trimmed() -> None:
    (block,) = parse_markdown("  one  \n two\n")
    assert isinstance(block, RawParagraph)
    assert [_text(line) for line in block.lines] == ["one", "two"]


def test_block_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(markdown, "MAX_MD_BLOCKS", 2)
    with pytest.raises(AsfbError) as info:
        parse_markdown("# a\n# b\n# c\n")
    assert info.value.code is ErrorCode.E_MD_BLOCKS


# Literal fields come from the parser's own vocabulary, not from the source.
_LITERAL_FIELDS = frozenset({"quote", "level", "depth", "ordered", "task", "fence", "align"})


def test_every_block_text_is_untrusted() -> None:
    source = (
        "---\nm: 1\n---\n# h\nsetext\n===\n- [x] i\n```info\nc\n```\n"
        '    indent\n> q\n| a |\n:- |\n| b |\n[l]: /d "t"\n<p>x\n\n---\n'
    )
    for block in parse_markdown(source):
        for name in block.__slots__:
            if name in _LITERAL_FIELDS:
                continue
            value = getattr(block, name)
            values = value if isinstance(value, tuple) else (value,)
            for item in values:
                cells = item if isinstance(item, tuple) else (item,)
                for cell in cells:
                    if cell is not None:
                        assert isinstance(cell, Untrusted), f"{type(block).__name__}.{name}"


def test_line_scanning_stays_fast_on_pathological_lines() -> None:
    for pattern in ("|", "#", "[", "`", "-", "a|b", "-|-", "x"):
        text = pattern * (900_000 // len(pattern)) + "\n"
        started = time.monotonic()
        parse_markdown(text)
        assert time.monotonic() - started < 5.0


def test_a_pathological_quote_run_is_refused_quickly() -> None:
    started = time.monotonic()
    with pytest.raises(AsfbError) as info:
        parse_markdown("> " * 300_000 + "\n")
    assert info.value.code is ErrorCode.E_MD_DEPTH
    assert time.monotonic() - started < 5.0
