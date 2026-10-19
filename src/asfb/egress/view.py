"""Censored view: the text an agent reads. Built only from the emitted JSON, and only in egress.

The renderer prints tokens, vocabulary terms and fixed literals of this module, nothing else.
Input is re-validated and must reserialize to the same emitted string, so a tampered document
fails here. Failures raise EgressError(E_VIEW) outside any except block.
"""

from typing import Final, NewType

from asfb.egress.gate import Emitted, emit
from asfb.errors import EgressError, ErrorCode
from asfb.schema.doc import DocV1
from asfb.schema.html import Attr, AttrValue, Doctype, Element, Opaque, OpaqueForm, Text, Url
from asfb.schema.html import Node as SchemaNode
from asfb.types.token import Token
from asfb.types.values import EnumValue
from asfb.vocab import VOID_ELEMENTS, html_registry

CensoredView = NewType("CensoredView", str)

_ELEMENTS: Final = "html.elements"
_INDENT: Final = "  "


class _ViewError(Exception):
    """Internal: the document cannot be printed. Carries no input text."""


def _name(value: EnumValue | Token) -> str:
    if isinstance(value, EnumValue):
        return value.term
    return value


def _is_void(tag: EnumValue | Token) -> bool:
    return isinstance(tag, EnumValue) and tag.vocab == _ELEMENTS and tag.term in VOID_ELEMENTS


def _url(url: Url) -> str:
    parts: list[str] = []
    if url.scheme is not None:
        parts.append(url.scheme.term + ":")
    if url.authority is not None:
        parts.append("//" + url.authority)
    if url.rooted:
        parts.append("/")
    parts.append("/".join(url.path))
    if url.trailing_slash:
        parts.append("/")
    if url.query is not None:
        parts.append("?" + url.query)
    if url.fragment is not None:
        parts.append("#" + url.fragment)
    return "".join(parts)


def _value(value: AttrValue) -> str:
    if isinstance(value, Url):
        return _url(value)
    if isinstance(value, tuple):
        return " ".join(_name(item) for item in value)
    if isinstance(value, bool):
        raise _ViewError
    return _name(value)


def _attr(attr: Attr) -> str:
    name = _name(attr.name)
    if attr.value is True:
        return name
    return f'{name}="{_value(attr.value)}"'


def _open(element: Element) -> str:
    attrs = "".join(" " + _attr(attr) for attr in element.attrs)
    return f"<{_name(element.tag)}{attrs}>"


def _opaque(node: Opaque) -> str:
    if node.form is OpaqueForm.COMMENT:
        return f"<!-- {node.text} -->"
    return f"<!--{node.form.value} {node.text}-->"


def _doctype(node: Doctype) -> str:
    return "<!DOCTYPE html>" if node.html else "<!DOCTYPE>"


def _lines(node: SchemaNode, depth: int, out: list[str]) -> None:
    pad = _INDENT * depth
    if isinstance(node, Text):
        out.append(pad + node.text)
    elif isinstance(node, Opaque):
        out.append(pad + _opaque(node))
    elif isinstance(node, Doctype):
        out.append(pad + _doctype(node))
    else:
        _element_lines(node, depth, out)


def _element_lines(element: Element, depth: int, out: list[str]) -> None:
    pad = _INDENT * depth
    head = _open(element)
    if _is_void(element.tag):
        if element.children:
            raise _ViewError
        out.append(pad + head)
        return
    name = _name(element.tag)
    if all(isinstance(child, Text) for child in element.children):
        inner = " ".join(child.text for child in element.children if isinstance(child, Text))
        out.append(f"{pad}{head}{inner}</{name}>")
        return
    out.append(pad + head)
    for child in element.children:
        _lines(child, depth + 1, out)
    out.append(f"{pad}</{name}>")


def _document_text(model: DocV1) -> str:
    out: list[str] = []
    for node in model.body:
        _lines(node, 0, out)
    return "\n".join(out)


def _render(emitted: Emitted) -> str | ErrorCode:
    try:
        model = DocV1.model_validate_json(emitted, strict=True)
        if emit(model, vocab=html_registry()) != emitted:
            return ErrorCode.E_VIEW
        return _document_text(model)
    except _ViewError:
        return ErrorCode.E_VIEW
    except Exception:  # noqa: BLE001
        return ErrorCode.E_VIEW


def render(emitted: Emitted, /) -> CensoredView:
    """Print the censored view of an emitted document. Raises EgressError(E_VIEW)."""
    outcome = _render(emitted)
    if isinstance(outcome, ErrorCode):
        raise EgressError(outcome)
    return CensoredView(outcome)
