"""Raw HTML tree built with the stdlib html.parser (convert_charrefs=True).

Known gap: M1 runs in-process. The limits in limits.py bound depth, node count and
attribute count, but there is no worker process and no CPU, memory or time limit.
A bug in html.parser can break the no-leak guarantee; process isolation is later work.

Every string in the returned tree is wrapped in Untrusted before it leaves this module.
The parser works on plain str from the caller, never on Untrusted content.
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest.limits import MAX_ATTRS, MAX_DEPTH, MAX_NODES
from asfb.types.untrusted import Untrusted
from asfb.vocab import VOID_ELEMENTS

_DOCTYPE: Final = "DOCTYPE"
_CDATA: Final = "CDATA["


@dataclass(frozen=True, slots=True, eq=False)
class Element:
    tag: Untrusted[str]
    attributes: tuple[tuple[Untrusted[str], Untrusted[str] | None], ...]
    children: tuple["Node", ...]


@dataclass(frozen=True, slots=True, eq=False)
class Text:
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class Comment:
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class CData:
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class Declaration:
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class ProcessingInstruction:
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class Doctype:
    text: Untrusted[str]


type Node = Element | Text | Comment | CData | Declaration | ProcessingInstruction | Doctype


@dataclass(slots=True, eq=False)
class _Open:
    tag: str
    attributes: dict[str, str | None]
    children: list["_Open | Node"] = field(default_factory=list)


class _Bound(Exception):
    """Carries a static code out of a parser callback. Holds no input text."""

    def __init__(self, code: ErrorCode, /) -> None:
        super().__init__(code.value)
        self.code = code


def _freeze(frame: _Open) -> Element:
    return Element(
        tag=Untrusted(frame.tag),
        attributes=tuple(
            (Untrusted(name), None if value is None else Untrusted(value))
            for name, value in frame.attributes.items()
        ),
        children=tuple(
            _freeze(child) if isinstance(child, _Open) else child for child in frame.children
        ),
    )


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._root = _Open("", {})
        self._frames: list[_Open] = [self._root]
        self._chunks: list[str] = []
        self._total = 0

    def _add(self, node: _Open | Node) -> None:
        self._total += 1
        if self._total > MAX_NODES:
            raise _Bound(ErrorCode.E_HTML_NODES)
        self._frames[-1].children.append(node)

    def _flush(self) -> None:
        if self._chunks:
            text = "".join(self._chunks)
            self._chunks.clear()
            self._add(Text(Untrusted(text)))

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._flush()
        if len(self._frames) > MAX_DEPTH:
            raise _Bound(ErrorCode.E_HTML_DEPTH)
        attributes: dict[str, str | None] = {}
        for name, value in attrs:
            attributes.setdefault(name, value)
        if len(attributes) > MAX_ATTRS:
            raise _Bound(ErrorCode.E_HTML_ATTRS)
        element = _Open(tag, attributes)
        self._add(element)
        if tag not in VOID_ELEMENTS:
            self._frames.append(element)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._frames) - 1, 0, -1):
            if self._frames[index].tag == tag:
                self._flush()
                del self._frames[index:]
                return

    def handle_comment(self, data: str) -> None:
        self._flush()
        self._add(Comment(Untrusted(data)))

    def handle_decl(self, decl: str) -> None:
        self._flush()
        if decl[: len(_DOCTYPE)].upper() == _DOCTYPE:
            self._add(Doctype(Untrusted(decl[len(_DOCTYPE) :].strip())))
        else:
            self._add(Declaration(Untrusted(decl)))

    def handle_pi(self, data: str) -> None:
        self._flush()
        self._add(ProcessingInstruction(Untrusted(data)))

    def unknown_decl(self, data: str) -> None:
        self._flush()
        if data.startswith(_CDATA):
            self._add(CData(Untrusted(data[len(_CDATA) :])))
        else:
            self._add(Declaration(Untrusted(data)))

    def close(self) -> None:
        super().close()
        self._flush()

    def tree(self) -> tuple[Node, ...]:
        return tuple(
            _freeze(child) if isinstance(child, _Open) else child for child in self._root.children
        )


def _feed(builder: _Builder, text: str) -> ErrorCode | None:
    try:
        builder.feed(text)
        builder.close()
    except _Bound as bound:
        return bound.code
    except Exception:  # noqa: BLE001
        return ErrorCode.E_HTML_PARSE
    return None


def parse_html(text: str, /) -> tuple[Node, ...]:
    """Build the raw tree of top-level nodes. Raises AsfbError with a static code."""
    builder = _Builder()
    code = _feed(builder, text)
    if code is not None:
        raise AsfbError(code)
    return builder.tree()
