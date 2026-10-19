"""Ingest: read HTML into a raw tree whose strings are all Untrusted."""

from asfb.ingest.html import (
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
from asfb.ingest.source import read_path, read_text

__all__ = [
    "CData",
    "Comment",
    "Declaration",
    "Doctype",
    "Element",
    "Node",
    "ProcessingInstruction",
    "Text",
    "parse_html",
    "read_path",
    "read_text",
]
