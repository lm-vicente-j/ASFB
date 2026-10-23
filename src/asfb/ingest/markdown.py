"""Markdown block scanner: source lines become raw blocks, and every block text stays Untrusted.

The subset is CommonMark/GFM-like: front matter, ATX and setext headings, lists with task marks,
fenced and indented code, block quotes, tables, HTML blocks, link definitions and rules. Inline
syntax is not read here; sanitize/markdown.py reads the inlines of each block text.

Rules are tried in the order of the spec for each line. The patterns are linear; the ATX heading
and indentation rules are plain string scans, so no line costs more than its length. Public
functions raise AsfbError outside any except block. Inner code raises through _Scanner and
parse_markdown turns the code into the error after the handler.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest.limits import MAX_LIST_DEPTH, MAX_MD_BLOCKS, MAX_QUOTE
from asfb.ingest.text import plain_lines
from asfb.types.untrusted import Untrusted

Align = Literal["none", "left", "center", "right"]
Task = Literal["none", "open", "done"]
_TAB: Final = 4
_TABLE_COLUMNS: Final = 64
_QUOTE_RE: Final = re.compile(r"^ {0,3}> ?")
_FENCE_OPEN_RE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_SETEXT_RE: Final = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_RULE_RE: Final = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_ITEM_RE: Final = re.compile(r"^([ \t]*)([-*+]|[0-9]{1,9}[.)])[ \t]+(.*)$")
_LINK_DEF_RE: Final = re.compile(
    r"^ {0,3}\[([^\]]{1,999})\]:[ \t]*(<[^>\n]*>|\S+)"
    r"(?:[ \t]+(\"[^\"]*\"|'[^']*'|\([^)]*\)))?[ \t]*$"
)
_HTML_RE: Final = re.compile(r"^ {0,3}<[A-Za-z/!?]")
_DELIMITER_RE: Final = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_CELL_SPLIT_RE: Final = re.compile(r"(?<!\\)\|")
_ALIGNS: Final[dict[str, Align]] = {"": "none", "L": "left", "R": "right", "C": "center"}
_TASKS: Final[dict[str, Literal["open", "done"]]] = {"[ ] ": "open", "[x] ": "done", "[X] ": "done"}


@dataclass(frozen=True, slots=True, eq=False)
class RawHeading:
    quote: int
    level: int
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class RawParagraph:
    quote: int
    lines: tuple[Untrusted[str], ...]


@dataclass(frozen=True, slots=True, eq=False)
class RawItem:
    quote: int
    depth: int
    ordered: bool
    task: Task
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class RawCode:
    quote: int
    fence: Literal["backtick", "tilde", "indent"]
    info: Untrusted[str] | None
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class RawTable:
    quote: int
    align: tuple[Align, ...]
    header: tuple[Untrusted[str], ...]
    rows: tuple[tuple[Untrusted[str], ...], ...]


@dataclass(frozen=True, slots=True, eq=False)
class RawRule:
    quote: int


@dataclass(frozen=True, slots=True, eq=False)
class RawHtml:
    quote: int
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class RawFrontMatter:
    text: Untrusted[str]


@dataclass(frozen=True, slots=True, eq=False)
class RawLinkDef:
    quote: int
    label: Untrusted[str]
    dest: Untrusted[str] | None
    title: Untrusted[str] | None


type RawBlock = (
    RawHeading
    | RawParagraph
    | RawItem
    | RawCode
    | RawTable
    | RawRule
    | RawHtml
    | RawFrontMatter
    | RawLinkDef
)


def _quote(raw: str) -> tuple[int, str]:
    """The block quote depth of a line and the rest of it. Deeper than MAX_QUOTE is refused."""
    depth = 0
    rest = raw
    while True:
        found = _QUOTE_RE.match(rest)
        if found is None:
            return depth, rest
        depth += 1
        if depth > MAX_QUOTE:
            raise AsfbError(ErrorCode.E_MD_DEPTH)
        rest = rest[found.end() :]


def _indent(text: str) -> tuple[int, int]:
    """The width of the leading spaces and tabs (a tab is four columns) and their length."""
    columns = 0
    index = 0
    while index < len(text) and text[index] in " \t":
        columns += 1 if text[index] == " " else _TAB
        index += 1
    return columns, index


def _drop_columns(text: str, columns: int) -> str:
    """The line without its first `columns` columns of indentation."""
    removed = 0
    index = 0
    while index < len(text) and removed < columns and text[index] in " \t":
        removed += 1 if text[index] == " " else _TAB
        index += 1
    return text[index:]


def _atx(content: str) -> tuple[int, str] | None:
    """An ATX heading: its level and its text without the optional closing run of hashes."""
    stripped = content.lstrip(" ")
    if len(content) - len(stripped) > 3:
        return None
    level = len(stripped) - len(stripped.lstrip("#"))
    if not 1 <= level <= 6:
        return None
    rest = stripped[level:]
    if rest and rest[0] not in " \t":
        return None
    text = rest.strip(" \t")
    end = len(text)
    while end > 0 and text[end - 1] == "#":
        end -= 1
    if end < len(text) and (end == 0 or text[end - 1] in " \t"):
        text = text[:end].rstrip(" \t")
    return level, text


def _cells(line: str) -> list[str]:
    """The cells of a table line: one leading and one trailing pipe are optional."""
    body = line.strip(" \t")
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.strip(" \t") for cell in _CELL_SPLIT_RE.split(body)]


def _aligns(line: str) -> tuple[Align, ...]:
    marks: list[Align] = []
    for cell in _cells(line):
        left = cell.startswith(":")
        right = cell.endswith(":")
        key = "C" if left and right else "L" if left else "R" if right else ""
        marks.append(_ALIGNS[key])
    return tuple(marks)


def _link_title(raw: str | None) -> str | None:
    if raw is None:
        return None
    return raw[1:-1]


def _link_dest(raw: str) -> str:
    return raw[1:-1] if raw.startswith("<") else raw


def _first_word(text: str) -> str | None:
    word = text.strip(" \t").split(" ", 1)[0].split("\t", 1)[0]
    return word or None


def _task(text: str) -> tuple[Task, str]:
    for mark, state in _TASKS.items():
        if text.startswith(mark):
            return state, text[len(mark) :]
    return "none", text


@dataclass(slots=True, eq=False)
class _Fence:
    char: str
    length: int
    quote: int
    info: str | None
    lines: list[str]


@dataclass(slots=True, eq=False)
class _Table:
    quote: int
    align: tuple[Align, ...]
    header: tuple[str, ...]
    rows: list[tuple[str, ...]]


class _Scanner:
    """One pass over the lines. Holds the open block and the blocks emitted so far."""

    def __init__(self) -> None:
        self.blocks: list[RawBlock] = []
        self.para: list[str] | None = None
        self.para_quote = 0
        self.table: _Table | None = None
        self.fence: _Fence | None = None
        self.html: tuple[int, list[str]] | None = None
        self.indent: tuple[int, list[str]] | None = None

    def emit(self, block: RawBlock) -> None:
        self.blocks.append(block)
        if len(self.blocks) > MAX_MD_BLOCKS:
            raise AsfbError(ErrorCode.E_MD_BLOCKS)

    def _close_para(self) -> None:
        if self.para is not None:
            lines = tuple(Untrusted(line) for line in self.para)
            self.emit(RawParagraph(quote=self.para_quote, lines=lines))
            self.para = None

    def _close_table(self) -> None:
        if self.table is not None:
            table = self.table
            self.emit(
                RawTable(
                    quote=table.quote,
                    align=table.align,
                    header=tuple(Untrusted(cell) for cell in table.header),
                    rows=tuple(tuple(Untrusted(cell) for cell in row) for row in table.rows),
                )
            )
            self.table = None

    def _close_fence(self) -> None:
        if self.fence is not None:
            fence = self.fence
            info = None if fence.info is None else Untrusted(fence.info)
            kind: Literal["backtick", "tilde"] = "tilde" if fence.char == "~" else "backtick"
            text = Untrusted("\n".join(fence.lines))
            self.emit(RawCode(quote=fence.quote, fence=kind, info=info, text=text))
            self.fence = None

    def _close_html(self) -> None:
        if self.html is not None:
            quote, lines = self.html
            self.emit(RawHtml(quote=quote, text=Untrusted("\n".join(lines))))
            self.html = None

    def _close_indent(self) -> None:
        if self.indent is not None:
            quote, lines = self.indent
            text = Untrusted("\n".join(lines))
            self.emit(RawCode(quote=quote, fence="indent", info=None, text=text))
            self.indent = None

    def _settle(self, quote: int) -> None:
        """A block continues only on lines of its own quote depth."""
        if self.para is not None and self.para_quote != quote:
            self._close_para()
        if self.table is not None and self.table.quote != quote:
            self._close_table()
        if self.fence is not None and self.fence.quote != quote:
            self._close_fence()
        if self.html is not None and self.html[0] != quote:
            self._close_html()
        if self.indent is not None and self.indent[0] != quote:
            self._close_indent()

    def feed(self, raw: str) -> None:
        quote, rest = _quote(raw)
        self._settle(quote)
        if self.fence is not None:
            self._fence_line(self.fence, rest)
            return
        if self.html is not None:
            if not rest.strip(" \t"):
                self._close_html()
            else:
                self.html[1].append(rest)
            return
        if not rest.strip(" \t"):
            self._close_para()
            self._close_table()
            self._close_indent()
            return
        if self.indent is not None:
            columns, _length = _indent(rest)
            if columns >= _TAB:
                self.indent[1].append(_drop_columns(rest, _TAB))
                return
            self._close_indent()
        if self.table is not None:
            self._table_line(self.table, quote, rest)
            return
        self._block(quote, rest)

    def _fence_line(self, fence: _Fence, rest: str) -> None:
        found = _FENCE_CLOSE_RE.match(rest)
        if found is not None and _closes(fence, found.group(1)):
            self._close_fence()
            return
        fence.lines.append(rest)

    def _table_line(self, table: _Table, quote: int, rest: str) -> None:
        if "|" not in rest:
            self._close_table()
            self._block(quote, rest)
            return
        cells = _cells(rest)[: len(table.align)]
        cells += [""] * (len(table.align) - len(cells))
        table.rows.append(tuple(cells))

    def _block(self, quote: int, rest: str) -> None:
        opening = _FENCE_OPEN_RE.match(rest)
        if opening is not None and self._open_fence(quote, opening):
            return
        found = _atx(rest)
        if found is not None:
            self._close_para()
            level, text = found
            self.emit(RawHeading(quote=quote, level=level, text=Untrusted(text)))
            return
        if self.para is not None and self.para_quote == quote and _SETEXT_RE.match(rest):
            text = " ".join(line.strip(" \t") for line in self.para)
            self.para = None
            level = 1 if rest.lstrip(" ").startswith("=") else 2
            self.emit(RawHeading(quote=quote, level=level, text=Untrusted(text)))
            return
        if self._table_head(quote, rest):
            return
        if _RULE_RE.match(rest):
            self._close_para()
            self.emit(RawRule(quote=quote))
            return
        if self._item(quote, rest):
            return
        if self._link_def(quote, rest):
            return
        if _HTML_RE.match(rest):
            self._close_para()
            self.html = (quote, [rest])
            return
        columns, _length = _indent(rest)
        if columns >= _TAB and self.para is None and not self._after_item():
            self.indent = (quote, [_drop_columns(rest, _TAB)])
            return
        self._paragraph_line(quote, rest)

    def _open_fence(self, quote: int, found: re.Match[str]) -> bool:
        marks, tail = found.group(1), found.group(2)
        if marks[0] == "`" and "`" in tail:
            return False
        self._close_para()
        self.fence = _Fence(
            char=marks[0], length=len(marks), quote=quote, info=_first_word(tail), lines=[]
        )
        return True

    def _after_item(self) -> bool:
        return bool(self.blocks) and isinstance(self.blocks[-1], RawItem)

    def _item(self, quote: int, rest: str) -> bool:
        found = _ITEM_RE.match(rest)
        if found is None:
            return False
        self._close_para()
        self._close_table()
        columns, _length = _indent(found.group(1))
        marker = found.group(2)
        depth = min(columns // 2, MAX_LIST_DEPTH)
        task, text = _task(found.group(3))
        self.emit(
            RawItem(
                quote=quote,
                depth=depth,
                ordered=marker[0].isdigit(),
                task=task,
                text=Untrusted(text),
            )
        )
        return True

    def _link_def(self, quote: int, rest: str) -> bool:
        found = _LINK_DEF_RE.match(rest)
        if found is None:
            return False
        self._close_para()
        self.emit(
            RawLinkDef(
                quote=quote,
                label=Untrusted(found.group(1)),
                dest=Untrusted(_link_dest(found.group(2))),
                title=_optional(_link_title(found.group(3))),
            )
        )
        return True

    def _table_head(self, quote: int, rest: str) -> bool:
        para = self.para
        if para is None or len(para) != 1 or "|" not in para[0] or not _DELIMITER_RE.match(rest):
            return False
        header = _cells(para[0])
        if len(header) != len(_cells(rest)) or len(header) > _TABLE_COLUMNS:
            return False
        self.para = None
        self.table = _Table(quote=quote, align=_aligns(rest), header=tuple(header), rows=[])
        return True

    def _paragraph_line(self, quote: int, rest: str) -> None:
        if self.para is not None and self.para_quote == quote:
            self.para.append(rest.strip(" \t"))
            return
        self._close_para()
        self.para = [rest.strip(" \t")]
        self.para_quote = quote

    def finish(self) -> tuple[RawBlock, ...]:
        self._close_fence()
        self._close_html()
        self._close_indent()
        self._close_table()
        self._close_para()
        return tuple(self.blocks)


def _closes(fence: _Fence, marks: str) -> bool:
    """A closing run uses the fence's character and is at least as long as the opening one."""
    return marks[0] == fence.char and len(marks) >= fence.length


def _optional(text: str | None) -> Untrusted[str] | None:
    return None if text is None else Untrusted(text)


def _front_matter_end(lines: tuple[str, ...]) -> int | None:
    """The index of the closing line of a front matter that starts on line 0, if there is one."""
    if not lines or lines[0] != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index] in ("---", "..."):
            return index
    return None


def _scan(text: str) -> tuple[RawBlock, ...]:
    lines = plain_lines(text)
    scanner = _Scanner()
    start = 0
    end = _front_matter_end(lines)
    if end is not None:
        scanner.emit(RawFrontMatter(text=Untrusted("\n".join(lines[1:end]))))
        start = end + 1
    for line in lines[start:]:
        scanner.feed(line)
    return scanner.finish()


def parse_markdown(text: str, /) -> tuple[RawBlock, ...]:
    """Scan a markdown text into its raw blocks. Raises AsfbError with a static code."""
    try:
        return _scan(text)
    except AsfbError as error:
        code = error.code
    raise AsfbError(code)
