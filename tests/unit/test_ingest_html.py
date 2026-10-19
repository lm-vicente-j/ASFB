import dataclasses
from typing import Any

import pytest

from asfb.ingest import (
    CData,
    Comment,
    Declaration,
    Doctype,
    Element,
    Node,
    ProcessingInstruction,
    Text,
    parse_html,
)
from asfb.types.untrusted import Untrusted, unwrap


def _show(node: Node) -> tuple[Any, ...]:
    match node:
        case Element(tag=tag, attributes=attributes, children=children):
            pairs = tuple(
                (unwrap(name), None if value is None else unwrap(value))
                for name, value in attributes
            )
            return ("element", unwrap(tag), pairs, tuple(_show(child) for child in children))
        case Text(text=text):
            return ("text", unwrap(text))
        case Comment(text=text):
            return ("comment", unwrap(text))
        case CData(text=text):
            return ("cdata", unwrap(text))
        case Declaration(text=text):
            return ("decl", unwrap(text))
        case ProcessingInstruction(text=text):
            return ("pi", unwrap(text))
        case Doctype(text=text):
            return ("doctype", unwrap(text))


def _tree(html: str) -> tuple[Any, ...]:
    return tuple(_show(node) for node in parse_html(html))


def el(tag: str, *children: Any, attrs: tuple[tuple[str, str | None], ...] = ()) -> tuple[Any, ...]:
    return ("element", tag, attrs, children)


def text(value: str) -> tuple[Any, ...]:
    return ("text", value)


def _strings(node: Node) -> list[object]:
    match node:
        case Element(tag=tag, attributes=attributes, children=children):
            found: list[object] = [tag]
            for name, value in attributes:
                found.append(name)
                if value is not None:
                    found.append(value)
            for child in children:
                found.extend(_strings(child))
            return found
        case Text(text=value) | Comment(text=value) | CData(text=value):
            return [value]
        case Declaration(text=value) | ProcessingInstruction(text=value) | Doctype(text=value):
            return [value]


def test_empty_document_has_no_nodes() -> None:
    assert parse_html("") == ()


def test_void_elements_are_leaves_and_never_open() -> None:
    assert _tree('<p>a<br>b<img src="x">c</p>') == (
        el("p", text("a"), el("br"), text("b"), el("img", attrs=(("src", "x"),)), text("c")),
    )


def test_self_closing_void_element_does_not_capture_following_text() -> None:
    assert _tree("<p>a<br/>b</p>") == (el("p", text("a"), el("br"), text("b")),)


def test_end_tag_of_void_element_is_ignored() -> None:
    assert _tree("<p>a</br>b</p>") == (el("p", text("ab")),)


def test_nesting_builds_children_in_order() -> None:
    assert _tree("<div><span>x</span><b>y</b></div>") == (
        el("div", el("span", text("x")), el("b", text("y"))),
    )


def test_stray_end_tag_is_ignored_and_text_still_merges() -> None:
    assert _tree("<div>a</span>b</div>") == (el("div", text("ab")),)


def test_end_tag_closes_up_to_nearest_matching_element() -> None:
    assert _tree("<div><span>a</div>b") == (el("div", el("span", text("a"))), text("b"))


def test_unclosed_elements_close_at_end_of_input() -> None:
    assert _tree("<div><p>x") == (el("div", el("p", text("x"))),)


def test_tag_and_attribute_names_are_lowercased() -> None:
    assert _tree('<DIV CLASS="x"></DIV>') == (el("div", attrs=(("class", "x"),)),)


def test_duplicate_attributes_keep_the_first_value() -> None:
    assert _tree('<a href="1" href="2" title="t">x</a>') == (
        el("a", text("x"), attrs=(("href", "1"), ("title", "t"))),
    )


def test_attribute_without_value_is_none() -> None:
    assert _tree('<input disabled value="">') == (
        el("input", attrs=(("disabled", None), ("value", ""))),
    )


def test_entities_are_decoded_in_text_and_attribute_values() -> None:
    assert _tree('<p title="a&amp;b">&lt;&#65;&#x42;&amp;</p>') == (
        el("p", text("<AB&"), attrs=(("title", "a&b"),)),
    )


def test_adjacent_data_chunks_merge_into_one_text_node() -> None:
    assert _tree("<p>a < b &amp; c</p>") == (el("p", text("a < b & c")),)


def test_comment_splits_text_nodes() -> None:
    assert _tree("<p>a<!--c-->b</p>") == (el("p", text("a"), ("comment", "c"), text("b")),)


def test_top_level_comment_and_doctype() -> None:
    assert _tree("<!DOCTYPE html><!-- hi --><p>x</p>") == (
        ("doctype", "html"),
        ("comment", " hi "),
        el("p", text("x")),
    )


def test_doctype_text_is_kept_as_written() -> None:
    assert _tree("<!doctype HTML>") == (("doctype", "HTML"),)


def test_script_content_is_one_text_node() -> None:
    assert _tree("<script>if (a<b) x();</script>") == (el("script", text("if (a<b) x();")),)


def test_whitespace_only_text_is_kept_by_ingest() -> None:
    assert _tree("<p> </p>") == (el("p", text(" ")),)


def test_cdata_section_is_recognised() -> None:
    assert _tree("<![CDATA[x<y]]>") == (("cdata", "x<y"),)


def test_processing_instruction_is_recognised() -> None:
    assert _tree("<?php echo 1 ?>") == (("pi", "php echo 1 ?"),)


def test_nodes_are_frozen() -> None:
    (node,) = parse_html("<p>x</p>")
    name = "children"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(node, name, ())


def test_every_string_leaving_ingest_is_untrusted() -> None:
    html = (
        "<!DOCTYPE html><!--c--><![CDATA[d]]><?p q?><!ELEMENT e>"
        '<div id="a" hidden><span class="b">t</span></div>'
    )
    found: list[object] = []
    for node in parse_html(html):
        found.extend(_strings(node))
    assert found
    assert all(isinstance(value, Untrusted) for value in found)
