"""Limits through the public sanitize_html: each bound, bound+1, and a depth-64 document."""

import json
from typing import Any

import pytest

from asfb import sanitize_html
from asfb.api import Doc
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest.limits import MAX_ATTRS, MAX_BYTES, MAX_DEPTH, MAX_NODES
from asfb.schema.doc import DocV1
from asfb.schema.html import Element


def _html(doc: Doc) -> DocV1:
    assert isinstance(doc.model, DocV1)
    return doc.model


def _code_of(html: str) -> AsfbError:
    with pytest.raises(AsfbError) as info:
        sanitize_html(html)
    return info.value


def _assert_clean(error: AsfbError, code: ErrorCode) -> None:
    assert error.code is code
    assert error.args == (code.value,)
    assert str(error) == code.value
    assert repr(error) == f"AsfbError('{code.value}')"
    assert error.__cause__ is None
    assert error.__context__ is None


def _depth(nodes: tuple[Any, ...]) -> int:
    depth = 0
    current = nodes
    while current and isinstance(current[0], Element):
        depth += 1
        current = current[0].children
    return depth


def test_depth_at_max_is_accepted() -> None:
    doc = sanitize_html("<div>" * MAX_DEPTH + "x" + "</div>" * MAX_DEPTH)
    assert _depth(_html(doc).body) == MAX_DEPTH


def test_depth_one_over_max_is_refused() -> None:
    _assert_clean(_code_of("<div>" * (MAX_DEPTH + 1) + "x"), ErrorCode.E_HTML_DEPTH)


def test_depth_64_document_emits_and_renders() -> None:
    doc = sanitize_html("<div>" * MAX_DEPTH + "x" + "</div>" * MAX_DEPTH)
    assert json.loads(doc.json)["body"]
    view = str(doc.views.censored())
    assert view.count("<div>") == MAX_DEPTH
    assert view.count("</div>") == MAX_DEPTH
    innermost = view.splitlines()[MAX_DEPTH - 1]
    assert innermost.startswith("  " * (MAX_DEPTH - 1) + "<div>t_")
    assert innermost.endswith("</div>")


def test_nodes_at_max_are_accepted() -> None:
    doc = sanitize_html("<i></i>" * MAX_NODES)
    assert len(_html(doc).body) == MAX_NODES


def test_nodes_one_over_max_are_refused() -> None:
    _assert_clean(_code_of("<i></i>" * (MAX_NODES + 1)), ErrorCode.E_HTML_NODES)


def test_attributes_at_max_are_accepted() -> None:
    attrs = " ".join(f"a{index}=x" for index in range(MAX_ATTRS))
    doc = sanitize_html(f"<p {attrs}></p>")
    (node,) = _html(doc).body
    assert isinstance(node, Element)
    assert len(node.attrs) == MAX_ATTRS


def test_attributes_one_over_max_are_refused() -> None:
    attrs = " ".join(f"a{index}=x" for index in range(MAX_ATTRS + 1))
    _assert_clean(_code_of(f"<p {attrs}></p>"), ErrorCode.E_HTML_ATTRS)


def test_source_at_max_bytes_is_accepted() -> None:
    doc = sanitize_html("a" * MAX_BYTES)
    assert len(_html(doc).body) == 1


def test_source_one_byte_over_max_bytes_is_refused() -> None:
    _assert_clean(_code_of("a" * (MAX_BYTES + 1)), ErrorCode.E_SOURCE_SIZE)
