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
from asfb.ingest.text import Eol, plain_lines, split_lines

__all__ = [
    "CData",
    "Comment",
    "Declaration",
    "Doctype",
    "Element",
    "Eol",
    "Node",
    "ProcessingInstruction",
    "Text",
    "parse_html",
    "plain_lines",
    "read_path",
    "read_text",
    "split_lines",
]
