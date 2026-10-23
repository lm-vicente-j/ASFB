"""Log files, JSON Lines and plain text: split, parse and bound. Nothing here is output.

Every string leaves as Untrusted; only sanitize/log.py unwraps them, to build public words and
tokens. The one vocabulary check made here is the level word of a plain text header, because it
decides where the header ends and the message begins (vocab.log_level, nothing else is folded).
Instants (milliseconds since the epoch) are kept only so sanitize can compute deltas; they are
never emitted.

A plain text line that does not start with a header is a raw entry: one entry holding the whole
line. A line that starts with a space or tab continues the entry before it. In JSON Lines, a line
that is not one JSON object is a raw entry, and no error is raised for it. Limits are in limits.py
and any excess refuses the whole source. Public functions raise AsfbError outside any except block,
so no traceback carries log text.
"""

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Final, Literal

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import limits
from asfb.types.untrusted import Untrusted
from asfb.vocab import log_level

Format = Literal["jsonl", "text"]
_SUFFIXES: Final[dict[str, Format]] = {".jsonl": "jsonl", ".ndjson": "jsonl", ".log": "text"}
_ENCODING: Final = "utf-8-sig"
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_ISO_FULL: Final = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:[.,](\d{1,9}))?(Z|[+-]\d{2}(?::?\d{2})?)?"
)
_ISO_PREFIX: Final = re.compile(_ISO_FULL.pattern + r"(?=[ \t]|$)")
_LEVEL: Final = re.compile(r"\[?([A-Za-z0-9]{1,16})\]?:?(?=[ \t]|$)")
_LOGGER: Final = re.compile(r"([A-Za-z_][A-Za-z0-9_.\-]{0,127}):(?=[ \t]|$)")
_BLANK: Final = " \t"


@dataclass(frozen=True, slots=True, eq=False)
class LogGroup:
    """A nested JSON object kept as fields (depth at most limits.MAX_LOG_JSON_DEPTH)."""

    fields: "tuple[LogField, ...]"


# A value is a string (Untrusted, shown as a token), a number, a boolean, null or a nested group.
LogValue = Untrusted[str] | int | float | bool | None | LogGroup


@dataclass(frozen=True, slots=True, eq=False)
class LogField:
    """One field: its name (Untrusted until sanitize decides) and its value."""

    name: Untrusted[str]
    value: LogValue


@dataclass(frozen=True, slots=True, eq=False)
class LogEntry:
    """One record. Plain text headers fill message, level, instant and the logger field; JSON
    objects fill fields only. A raw entry carries its whole line as the message."""

    message: Untrusted[str] | None
    level: Untrusted[str] | None
    instant: int | None
    fields: tuple[LogField, ...]
    raw: bool


@dataclass(frozen=True, slots=True, eq=False)
class LogSource:
    """A log source: its format and its entries, in file order."""

    format: Format
    entries: tuple[LogEntry, ...]


@dataclass(slots=True)
class _Draft:
    """A plain text entry while its continuation lines are still arriving."""

    message: str | None
    level: str | None
    instant: int | None
    logger: str | None
    raw: bool


def format_of(name: str | None, /) -> Format | None:
    """The format of a file by its suffix: .jsonl and .ndjson are JSON Lines, .log is text.

    None (no name) is plain text. Any other suffix is None.
    """
    if name is None:
        return "text"
    return _SUFFIXES.get(os.path.splitext(name)[1].lower())


def parse_instant(text: object, /) -> int | None:
    """Milliseconds since the epoch of a full ISO 8601 timestamp, or None if it is not one.

    A timestamp without a zone is taken as UTC. Fractions keep their first three digits.
    """
    found = _ISO_FULL.fullmatch(text) if type(text) is str else None
    if found is None:
        return None
    zone = found.group(8)
    minutes = 0
    if zone is not None and zone != "Z":
        digits = zone[1:].replace(":", "")
        minutes = int(digits[:2]) * 60 + int(digits[2:4] or "0")
        if zone[0] == "-":
            minutes = -minutes
    fraction = found.group(7) or ""
    millis = int(fraction[:3].ljust(3, "0")) if fraction else 0
    try:
        moment = datetime(
            int(found.group(1)),
            int(found.group(2)),
            int(found.group(3)),
            int(found.group(4)),
            int(found.group(5)),
            int(found.group(6)),
            tzinfo=timezone(timedelta(minutes=minutes)),
        )
    except ValueError:
        return None
    delta = moment - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1000 + millis


def decoded(data: bytes, /) -> str | ErrorCode:
    """UTF-8 text of a log file (a BOM is dropped), or the static code. Never raises."""
    try:
        return data.decode(_ENCODING)
    except UnicodeDecodeError:
        return ErrorCode.E_LOG_ENCODING_UNSUPPORTED


def _lines(text: str) -> tuple[str, ...] | ErrorCode:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return ErrorCode.E_LOG_ENCODING_UNSUPPORTED
    if size > limits.MAX_LOG_BYTES:
        return ErrorCode.E_LOG_TOO_LARGE
    pieces = text.split("\n")
    if pieces and pieces[-1] == "":
        pieces.pop()
    lines: list[str] = []
    for piece in pieces:
        line = piece[:-1] if piece.endswith("\r") else piece
        if len(line.encode("utf-8")) > limits.MAX_LOG_LINE:
            return ErrorCode.E_LOG_LINE_TOO_LONG
        lines.append(line)
    return tuple(lines)


def _check_records(count: int) -> None:
    if count > limits.MAX_LOG_RECORDS:
        raise AsfbError(ErrorCode.E_LOG_TOO_MANY_RECORDS)


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(name: str) -> object:
    raise ValueError("non-finite number")


def _text(value: str) -> Untrusted[str]:
    # A lone surrogate cannot be encoded later, so the object is refused as a raw line instead.
    value.encode("utf-8")
    return Untrusted(value)


def _members(obj: dict[str, object], depth: int, counter: list[int]) -> tuple["LogField", ...]:
    fields: list[LogField] = []
    for key, value in obj.items():
        if len(key) > limits.MAX_LOG_NAME:
            raise AsfbError(ErrorCode.E_LOG_FIELD_NAME_TOO_LONG)
        counter[0] += 1
        if counter[0] > limits.MAX_LOG_FIELDS:
            raise AsfbError(ErrorCode.E_LOG_TOO_MANY_FIELDS)
        fields.append(LogField(_text(key), _value(value, depth, counter)))
    return tuple(fields)


def _value(value: object, depth: int, counter: list[int]) -> LogValue:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        return value
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, list):
        # Arrays are one token: their elements are never shown one by one.
        return Untrusted(json.dumps(value, separators=(",", ":"), allow_nan=False))
    if isinstance(value, dict):
        if depth + 1 > limits.MAX_LOG_JSON_DEPTH:
            # Past the depth limit the whole subtree is one token.
            return Untrusted(json.dumps(value, separators=(",", ":"), allow_nan=False))
        return LogGroup(_members(value, depth + 1, counter))
    raise ValueError("unexpected JSON value")


def _raw(line: str) -> LogEntry:
    return LogEntry(message=Untrusted(line), level=None, instant=None, fields=(), raw=True)


def _json_entry(line: str) -> LogEntry:
    try:
        value: object = json.loads(
            line, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant
        )
        if not isinstance(value, dict):
            raise ValueError("not an object")
        fields = _members(value, 1, [0])
    except (ValueError, RecursionError):
        return _raw(line)
    return LogEntry(message=None, level=None, instant=None, fields=fields, raw=False)


def _text_draft(line: str) -> _Draft:
    """The entry of a line that does not continue the previous one."""
    rest = line
    instant: int | None = None
    prefix = _ISO_PREFIX.match(rest)
    if prefix is not None:
        instant = parse_instant(prefix.group(0))
        if instant is None:
            # A stamp that is not a valid instant is not a header: the whole line stays raw.
            return _Draft(line, None, None, None, True)
        rest = rest[prefix.end() :].lstrip(_BLANK)
    level: str | None = None
    found = _LEVEL.match(rest)
    if found is not None and log_level(found.group(1)) is not None:
        level = found.group(1)
        rest = rest[found.end() :].lstrip(_BLANK)
    if prefix is None and level is None:
        return _Draft(line, None, None, None, True)
    logger: str | None = None
    named = _LOGGER.match(rest)
    if named is not None:
        logger = named.group(1)
        rest = rest[named.end() :].lstrip(_BLANK)
    return _Draft(rest or None, level, instant, logger, False)


def _freeze(draft: _Draft) -> LogEntry:
    fields: tuple[LogField, ...] = ()
    if draft.logger is not None:
        fields = (LogField(Untrusted("logger"), Untrusted(draft.logger)),)
    return LogEntry(
        message=None if draft.message is None else Untrusted(draft.message),
        level=None if draft.level is None else Untrusted(draft.level),
        instant=draft.instant,
        fields=fields,
        raw=draft.raw,
    )


def _entries_json(lines: tuple[str, ...]) -> tuple[LogEntry, ...]:
    entries: list[LogEntry] = []
    for line in lines:
        if line.strip(_BLANK) == "":
            continue
        entries.append(_json_entry(line))
        _check_records(len(entries))
    return tuple(entries)


def _entries_text(lines: tuple[str, ...]) -> tuple[LogEntry, ...]:
    drafts: list[_Draft] = []
    for line in lines:
        if line.strip(_BLANK) == "":
            continue
        if line[0] in _BLANK and drafts:
            last = drafts[-1]
            last.message = line if last.message is None else f"{last.message}\n{line}"
            continue
        drafts.append(_text_draft(line))
        _check_records(len(drafts))
    return tuple(_freeze(draft) for draft in drafts)


def _read(text: str, fmt: Format) -> LogSource | ErrorCode:
    if type(text) is not str or fmt not in ("jsonl", "text"):
        return ErrorCode.E_SOURCE_UNSUPPORTED
    lines = _lines(text)
    if isinstance(lines, ErrorCode):
        return lines
    try:
        entries = _entries_json(lines) if fmt == "jsonl" else _entries_text(lines)
    except AsfbError as error:
        return error.code
    return LogSource(format=fmt, entries=entries)


def read_text(text: str, fmt: Format, /) -> LogSource:
    """Split and parse a log given as text in the named format. Raises AsfbError with a code."""
    outcome = _read(text, fmt)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome
