"""Censored view: the text an agent reads. Built only from the emitted JSON, and only in egress.

The renderer prints tokens, document ids, vocabulary terms and fixed literals of this module,
nothing else. A document renders as HTML; a listing renders one line per entry:
`d_<id> t_<hex>/t_<hex>.html`, in the order the entries were given; a diff renders as a unified
diff (no context lines) of the two document views; a run renders a summary line and one line per
testcase.
Input is re-validated and must reserialize to the same emitted string, so a tampered document
fails here. Failures raise EgressError(E_VIEW) outside any except block.
"""

import json
from collections.abc import Callable
from typing import Any, Final, NewType

from asfb.egress.gate import Emitted, emit
from asfb.errors import EgressError, ErrorCode
from asfb.schema.code import CodeV1
from asfb.schema.code import Lexeme as CodeLexeme
from asfb.schema.db import DbColumn, DbIndex, DbTable, DbV1
from asfb.schema.db import Rows as DbRows
from asfb.schema.diff import DiffV1
from asfb.schema.doc import DocV1
from asfb.schema.html import Attr, AttrValue, Doctype, Element, Opaque, OpaqueForm, Text, Url
from asfb.schema.html import Node as SchemaNode
from asfb.schema.list import ListV1
from asfb.schema.log import LogField, LogNumber, LogRecord, LogV1, LogValue
from asfb.schema.md import (
    Block as _Block,
)
from asfb.schema.md import (
    CodeBlock,
    FrontMatter,
    Heading,
    HtmlBlock,
    Item,
    MdAutolink,
    MdCode,
    MdImage,
    MdLink,
    MdText,
    MdV1,
    Paragraph,
    Rule,
    Table,
)
from asfb.schema.run import Case, RunV1
from asfb.schema.text import TextV1
from asfb.types.token import Token
from asfb.types.values import EnumValue
from asfb.vocab import VOID_ELEMENTS, registry

CensoredView = NewType("CensoredView", str)

_ELEMENTS: Final = "html.elements"
_INDENT: Final = "  "
_Renderer = tuple[
    type[DocV1]
    | type[ListV1]
    | type[RunV1]
    | type[TextV1]
    | type[MdV1]
    | type[CodeV1]
    | type[LogV1]
    | type[DbV1],
    Callable[[Any], str],
]


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


def _location(case: Case) -> str:
    place = ".".join(case.suite)
    if case.name is not None:
        place = f"{place}::{case.name}" if place else case.name
    if case.params is not None:
        place += f"[{case.params}]"
    return place


def _case_text(case: Case) -> str:
    parts = [case.outcome.upper(), _location(case)]
    if case.error_type is not None:
        parts.append(_name(case.error_type))
    if case.message is not None:
        parts.append(case.message)
    return " ".join(part for part in parts if part)


def _run_text(run: RunV1) -> str:
    head = f"{run.tool.term} exit={run.exit} report={run.report}"
    if run.totals is not None:
        totals = run.totals
        head += (
            f" tests={totals.tests} failures={totals.failures}"
            f" errors={totals.errors} skipped={totals.skipped}"
        )
    return "\n".join([head, *(_case_text(case) for case in run.cases)])


def _document_text(model: DocV1) -> str:
    out: list[str] = []
    for node in model.body:
        _lines(node, 0, out)
    return "\n".join(out)


def _text_text(model: TextV1) -> str:
    # Paragraphs are separated by one empty line; each line is its indentation and its token.
    return "\n\n".join(
        "\n".join(" " * line.indent + line.text for line in paragraph.lines)
        for paragraph in model.paragraphs
    )


_DELIMITERS: Final = {"none": "---", "left": ":--", "right": "--:", "center": ":-:"}
_TASK_MARKS: Final = {"none": "", "open": "[ ] ", "done": "[x] "}
_Inline = MdText | MdCode | MdLink | MdImage | MdAutolink
_Cells = tuple[tuple[_Inline, ...], ...]


def _md_url(url: Url | Token | None) -> str:
    if url is None:
        return ""
    return _url(url) if isinstance(url, Url) else url


def _md_inline(node: _Inline) -> str:
    if isinstance(node, MdText):
        return node.text
    if isinstance(node, MdCode):
        return f"`{node.text}`"
    if isinstance(node, MdAutolink):
        return f"<{_md_url(node.url)}>"
    title = "" if node.title is None else f' "{node.title}"'
    if isinstance(node, MdImage):
        return f"![{node.alt or ''}]({_md_url(node.url)}{title})"
    return f"[{node.text or ''}]({_md_url(node.url)}{title})"


def _md_line(inlines: tuple[_Inline, ...]) -> str:
    return " ".join(_md_inline(node) for node in inlines)


def _md_code(block: CodeBlock) -> list[str]:
    if block.fence == "indent":
        return [] if block.text is None else ["    " + block.text]
    mark = "```" if block.fence == "backtick" else "~~~"
    lang = "" if block.lang is None else _name(block.lang)
    body = [] if block.text is None else [block.text]
    return [f"{mark}{lang}", *body, mark]


def _md_table(block: Table) -> list[str]:
    def row(cells: _Cells) -> str:
        return "| " + " | ".join(_md_line(cell) for cell in cells) + " |"

    delimiters = " | ".join(_DELIMITERS[align] for align in block.align)
    return [row(block.header), f"| {delimiters} |", *(row(cells) for cells in block.rows)]


def _md_block_lines(block: _Block) -> list[str]:
    """The lines of one block, before its block quote prefix is applied."""
    if isinstance(block, Heading):
        return [f"{'#' * block.level} {_md_line(block.inlines)}".rstrip()]
    if isinstance(block, Paragraph):
        return [_md_line(line) for line in block.lines]
    if isinstance(block, Item):
        mark = "1. " if block.ordered else "- "
        return ["  " * block.depth + mark + _TASK_MARKS[block.task] + _md_line(block.inlines)]
    if isinstance(block, CodeBlock):
        return _md_code(block)
    if isinstance(block, Table):
        return _md_table(block)
    if isinstance(block, Rule):
        return ["---"]
    if isinstance(block, HtmlBlock):
        return [block.text]
    if isinstance(block, FrontMatter):
        return ["---", *([] if block.text is None else [block.text]), "---"]
    title = "" if block.title is None else f' "{block.title}"'
    return [f"[{block.label}]: {_md_url(block.url)}{title}".rstrip()]


def _md_text(model: MdV1) -> str:
    # Blocks are separated by one empty line; a block quote prefixes every line of its block.
    blocks: list[str] = []
    for block in model.blocks:
        # Front matter has no quote depth: it is never inside a block quote.
        prefix = "" if isinstance(block, FrontMatter) else "> " * block.quote
        blocks.append("\n".join(prefix + line for line in _md_block_lines(block)))
    return "\n\n".join(blocks)


def _listing_text(listing: ListV1) -> str:
    return "\n".join(f"{entry.doc} {'/'.join(entry.path)}.{entry.ext}" for entry in listing.entries)


_PREFIX_WORDS: Final = {"str": "str", "bytes": "bytes", "fstring": "fstring", "raw": "raw"}
_INDENT_WIDTH: Final = 4


def _code_lexeme(lexeme: CodeLexeme) -> str | None:
    """The text of one lexeme in a code view; None for newline, indent and dedent."""
    if lexeme.kind in ("newline", "indent", "dedent"):
        return None
    if lexeme.kind == "comment":
        return f"# {lexeme.value}"
    if lexeme.kind == "string":
        return f"{_PREFIX_WORDS[lexeme.prefix or 'str']} {lexeme.value}"
    value = lexeme.value
    if value is None:
        return None
    if isinstance(value, EnumValue):
        return value.term
    return str(value)


def _code_text(model: CodeV1) -> str:
    """Canonical text: one line per source line (blank lines kept), tokens joined by one space and
    indented four spaces per depth. A comment on its own line takes the depth of the next token,
    since the tokenizer emits the comment before the indent it belongs to."""
    depth = 0
    lines: dict[int, tuple[int, list[str]]] = {}
    pending: list[tuple[int, str]] = []
    for lexeme in model.lexemes:
        if lexeme.kind in ("indent", "dedent"):
            depth = lexeme.depth or 0
            continue
        text = _code_lexeme(lexeme)
        if text is None:
            continue
        if lexeme.kind == "comment" and lexeme.line not in lines:
            pending.append((lexeme.line, text))
            continue
        for line, comment in pending:
            lines.setdefault(line, (depth, []))[1].append(comment)
        pending = []
        lines.setdefault(lexeme.line, (depth, []))[1].append(text)
    for line, comment in pending:
        lines.setdefault(line, (depth, []))[1].append(comment)
    body: list[str] = []
    for number in range(1, max(lines, default=0) + 1):
        if number not in lines:
            body.append("")
            continue
        level, parts = lines[number]
        body.append(" " * (_INDENT_WIDTH * level) + " ".join(parts))
    while body and body[-1] == "":
        body.pop()
    head = [] if model.file is None or model.ext is None else [f"{model.file}.{model.ext}"]
    return "\n".join([*head, *body])


def _log_value(value: LogValue) -> str:
    """A log value: a token or a public word, a literal, a bucket with its sign, or a group."""
    if isinstance(value, Token | EnumValue):
        return _name(value)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, LogNumber):
        return ("-" if value.sign == "neg" else "+") + f"b{value.bucket.index}"
    return "{" + " ".join(_log_field(field) for field in value.fields) + "}"


def _log_field(field: LogField) -> str:
    return f"{_name(field.name)}={_log_value(field.value)}"


def _log_record(record: LogRecord) -> str:
    """One record: its level, its time bucket, its message token, then its fields."""
    parts: list[str] = []
    if record.level is not None:
        parts.append(record.level.term)
    if record.time is not None:
        back = " back" if record.time.backwards else ""
        parts.append(f"dt=b{record.time.index}{back}")
    if record.message is not None:
        parts.append(record.message)
    parts.extend(_log_field(field) for field in record.fields)
    return " ".join(parts)


def _log_text(model: LogV1) -> str:
    head = f"log format={model.format} raw={model.raw}"
    return "\n".join([head, *(_log_record(record) for record in model.records)])


def _db_rows(rows: DbRows) -> str:
    """The row count of a table: a bucket with its index, or unknown."""
    return rows if isinstance(rows, str) else f"b{rows.index}"


def _db_column(column: DbColumn) -> str:
    """One column: name, declared type, affinity, then its flags and foreign keys."""
    declared = "-" if column.declared is None else _name(column.declared)
    parts = ["column", _name(column.name), declared, column.affinity.term]
    if column.notnull:
        parts.append("notnull")
    if column.pk:
        parts.append(f"pk={column.pk}")
    if column.default:
        parts.append("default")
    if column.hidden != "normal":
        parts.append(f"hidden={column.hidden}")
    for link in column.foreign_keys:
        target = "-" if link.column is None else link.column
        parts.append(f"fk={_name(link.table)}.{target}")
    return "  " + " ".join(parts)


def _db_index(index: DbIndex) -> str:
    columns = ["-" if column is None else column for column in index.columns]
    unique = " unique" if index.unique else ""
    return f"  index {_name(index.name)}{unique} ({','.join(columns)})"


def _db_table(table: DbTable) -> list[str]:
    without = " without_rowid" if table.without_rowid else ""
    lines = [f"table {_name(table.name)}{without} rows={_db_rows(table.rows)}"]
    lines.extend(_db_column(column) for column in table.columns)
    lines.extend(_db_index(index) for index in table.indexes)
    return lines


def _db_lines(model: DbV1) -> list[str]:
    wal = "yes" if model.wal_present else "no"
    lines = [f"db page_size=b{model.page_size.index} wal={wal}"]
    for table in model.tables:
        lines.extend(_db_table(table))
    lines.extend(f"view {_name(view)}" for view in model.views)
    lines.extend(
        f"trigger {_name(trigger.name)} on {_name(trigger.table)}" for trigger in model.triggers
    )
    return lines


def _db_text(model: DbV1) -> str:
    return "\n".join(_db_lines(model))


_RENDERERS: Final[dict[str, _Renderer]] = {
    "asfb.doc/1": (DocV1, _document_text),
    "asfb.list/1": (ListV1, _listing_text),
    "asfb.run/1": (RunV1, _run_text),
    "asfb.text/1": (TextV1, _text_text),
    "asfb.md/1": (MdV1, _md_text),
    "asfb.code/1": (CodeV1, _code_text),
    "asfb.log/1": (LogV1, _log_text),
    "asfb.db/1": (DbV1, _db_text),
}


def _render(emitted: Emitted) -> str | ErrorCode:
    try:
        renderer = _RENDERERS.get(json.loads(emitted).get("schema_id"))
        if renderer is None:
            return ErrorCode.E_VIEW
        model_type, text = renderer
        model = model_type.model_validate_json(emitted, strict=True)
        if emit(model, vocab=registry()) != emitted:
            return ErrorCode.E_VIEW
        return text(model)
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


def _view_lines(view: str) -> list[str]:
    return view.split("\n") if view else []


def _diff_text(diff: DiffV1, left: list[str], right: list[str]) -> str | ErrorCode:
    out = [f"--- {diff.a.doc}@{diff.a.revision}", f"+++ {diff.b.doc}@{diff.b.revision}"]
    for hunk in diff.hunks:
        a_size = hunk.a_end - hunk.a_start
        b_size = hunk.b_end - hunk.b_start
        if hunk.a_end > len(left) or hunk.b_end > len(right):
            return ErrorCode.E_VIEW
        if hunk.op == "insert" and (a_size != 0 or b_size == 0):
            return ErrorCode.E_VIEW
        if hunk.op == "delete" and (b_size != 0 or a_size == 0):
            return ErrorCode.E_VIEW
        if hunk.op == "replace" and (a_size == 0 or b_size == 0):
            return ErrorCode.E_VIEW
        out.append(f"@@ -{hunk.a_start + 1},{a_size} +{hunk.b_start + 1},{b_size} @@")
        out.extend("-" + line for line in left[hunk.a_start : hunk.a_end])
        out.extend("+" + line for line in right[hunk.b_start : hunk.b_end])
    return "\n".join(out)


def _code_unit(lexeme: CodeLexeme) -> str:
    """The text of one lexeme in a lexeme diff: structure keeps its name and depth."""
    if lexeme.kind == "newline":
        return "NEWLINE"
    if lexeme.kind in ("indent", "dedent"):
        return f"{lexeme.kind.upper()} {lexeme.depth}"
    text = _code_lexeme(lexeme)
    return "" if text is None else text


def _units(emitted: Emitted, units: str) -> list[str]:
    """The lines of a view, the lexeme units of a Python source, the record units of a log or the
    schema lines of a database."""
    if units == "lexeme":
        return list(code_units(emitted))
    if units == "record":
        return list(log_units(emitted))
    if units == "schema":
        return list(db_units(emitted))
    return _view_lines(str(render(emitted)))


def _render_diff(diff: Emitted, left: Emitted, right: Emitted) -> str | ErrorCode:
    try:
        model = DiffV1.model_validate_json(diff, strict=True)
        if emit(model, vocab=registry()) != diff:
            return ErrorCode.E_VIEW
        return _diff_text(model, _units(left, model.units), _units(right, model.units))
    except _ViewError:
        return ErrorCode.E_VIEW
    except Exception:  # noqa: BLE001
        return ErrorCode.E_VIEW


def _render_code_units(emitted: Emitted) -> tuple[str, ...] | ErrorCode:
    try:
        model = CodeV1.model_validate_json(emitted, strict=True)
        if emit(model, vocab=registry()) != emitted:
            return ErrorCode.E_VIEW
        return tuple(_code_unit(lexeme) for lexeme in model.lexemes)
    except Exception:  # noqa: BLE001
        return ErrorCode.E_VIEW


def code_units(emitted: Emitted, /) -> tuple[str, ...]:
    """One text per lexeme of an emitted Python source: the units a lexeme diff compares."""
    outcome = _render_code_units(emitted)
    if isinstance(outcome, ErrorCode):
        raise EgressError(outcome)
    return outcome


def _render_log_units(emitted: Emitted) -> tuple[str, ...] | ErrorCode:
    try:
        model = LogV1.model_validate_json(emitted, strict=True)
        if emit(model, vocab=registry()) != emitted:
            return ErrorCode.E_VIEW
        return tuple(_log_record(record) for record in model.records)
    except Exception:  # noqa: BLE001
        return ErrorCode.E_VIEW


def log_units(emitted: Emitted, /) -> tuple[str, ...]:
    """One text per record of an emitted log: the units a record diff compares."""
    outcome = _render_log_units(emitted)
    if isinstance(outcome, ErrorCode):
        raise EgressError(outcome)
    return outcome


def _render_db_units(emitted: Emitted) -> tuple[str, ...] | ErrorCode:
    try:
        model = DbV1.model_validate_json(emitted, strict=True)
        if emit(model, vocab=registry()) != emitted:
            return ErrorCode.E_VIEW
        return tuple(_db_lines(model))
    except Exception:  # noqa: BLE001
        return ErrorCode.E_VIEW


def db_units(emitted: Emitted, /) -> tuple[str, ...]:
    """One text per schema line of an emitted database: the units a schema diff compares."""
    outcome = _render_db_units(emitted)
    if isinstance(outcome, ErrorCode):
        raise EgressError(outcome)
    return outcome


def render_diff(diff: Emitted, a: Emitted, b: Emitted, /) -> CensoredView:
    """Print the unified diff of two emitted documents. Raises EgressError(E_VIEW)."""
    outcome = _render_diff(diff, a, b)
    if isinstance(outcome, ErrorCode):
        raise EgressError(outcome)
    return CensoredView(outcome)
