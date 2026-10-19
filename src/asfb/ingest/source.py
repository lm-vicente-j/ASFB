"""Read HTML from a path or take it from a string. Only .html and .htm paths are accepted.

The file contents and the string argument are plain str here, by design: they are only
fed to the parser. Every string that leaves ingest is wrapped in Untrusted by html.py.
Public functions raise AsfbError outside any except block; inner helpers return codes.
"""

import os
from pathlib import Path
from typing import Final

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest.html import Node, parse_html
from asfb.ingest.limits import MAX_BYTES

_SUFFIXES: Final = frozenset({".html", ".htm"})
_ENCODING: Final = "utf-8-sig"


def _parse(text: str) -> tuple[Node, ...] | ErrorCode:
    try:
        return parse_html(text)
    except AsfbError as error:
        return error.code


def _read_path(source: str | os.PathLike[str]) -> tuple[Node, ...] | ErrorCode:
    try:
        raw: object = os.fspath(source)
    except TypeError:
        return ErrorCode.E_SOURCE_UNSUPPORTED
    if not isinstance(raw, str):
        return ErrorCode.E_SOURCE_UNSUPPORTED
    path = Path(raw)
    if path.suffix.lower() not in _SUFFIXES:
        return ErrorCode.E_SOURCE_UNSUPPORTED
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except (OSError, ValueError):
        return ErrorCode.E_SOURCE_READ
    if len(data) > MAX_BYTES:
        return ErrorCode.E_SOURCE_SIZE
    try:
        text = data.decode(_ENCODING)
    except UnicodeDecodeError:
        return ErrorCode.E_SOURCE_DECODE
    return _parse(text)


def _read_text(html: str) -> tuple[Node, ...] | ErrorCode:
    if type(html) is not str:
        return ErrorCode.E_SOURCE_UNSUPPORTED
    try:
        size = len(html.encode("utf-8"))
    except UnicodeEncodeError:
        return ErrorCode.E_SOURCE_DECODE
    if size > MAX_BYTES:
        return ErrorCode.E_SOURCE_SIZE
    return _parse(html)


def read_path(source: str | os.PathLike[str], /) -> tuple[Node, ...]:
    """Read a .html or .htm file and return its raw tree. Raises AsfbError."""
    outcome = _read_path(source)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def read_text(html: str, /) -> tuple[Node, ...]:
    """Parse an HTML string and return its raw tree. Raises AsfbError."""
    outcome = _read_text(html)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome
