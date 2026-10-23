"""Public entry points. Each builds a Doc from a path or a string through the single pipeline.

The inner pipeline returns a Doc or an ErrorCode. The public functions raise AsfbError outside
any except block, so no traceback or context carries input text.

With isolate=True the parse and sanitize step runs in a worker process (asfb.sandbox). The main
process still does the file I/O and emits the Doc; the Doc equals the in-process result for the
same key. isolate=False (the default) keeps the in-process pipeline.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from asfb import ingest
from asfb.egress import CensoredView, Emitted, emit_before_render, render
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import db as ingest_db
from asfb.ingest import log as ingest_log
from asfb.ingest.code import decoded as decode_code
from asfb.ingest.code import read_text as read_code
from asfb.ingest.limits import MAX_BYTES, MAX_CODE_BYTES, MAX_LOG_BYTES
from asfb.ingest.markdown import parse_markdown
from asfb.sandbox.client import run_job
from asfb.sanitize.code import censor_code
from asfb.sanitize.db import with_wal
from asfb.sanitize.html import censor_document
from asfb.sanitize.log import censor_log_with_pairs
from asfb.sanitize.markdown import censor_md_with_pairs
from asfb.sanitize.text import censor_text_with_pairs
from asfb.schema.code import CodeV1
from asfb.schema.db import DbV1
from asfb.schema.doc import DocV1, Profile
from asfb.schema.log import LogV1
from asfb.schema.md import MdV1
from asfb.schema.text import TextV1
from asfb.types.key import SessionKey
from asfb.vocab import registry

_PROFILES: Final = {member.value: member for member in Profile}
_KINDS: Final[dict[str, str]] = {
    ".html": "html",
    ".htm": "html",
    ".txt": "text",
    ".md": "md",
    ".markdown": "md",
    ".py": "code",
    ".pyi": "code",
    ".log": "log",
    ".jsonl": "log",
    ".ndjson": "log",
    ".db": "db",
    ".sqlite": "db",
    ".sqlite3": "db",
}
_ENCODING: Final = "utf-8-sig"
_CODE_SUFFIXES: Final = frozenset({".py", ".pyi"})


@dataclass(frozen=True, slots=True, eq=False)
class Views:
    """Rendered forms of a document. Holds censored text only."""

    _censored: CensoredView

    def censored(self) -> CensoredView:
        return self._censored


@dataclass(frozen=True, slots=True, eq=False)
class Doc:
    """A censored document. Holds no original text and no key."""

    json: Emitted
    model: DocV1 | TextV1 | MdV1 | CodeV1 | LogV1 | DbV1
    profile: Profile
    views: Views


def _profile(value: object) -> Profile | ErrorCode:
    if isinstance(value, Profile):
        return value
    if type(value) is not str or value not in _PROFILES:
        return ErrorCode.E_PROFILE
    return _PROFILES[value]


def _session(profile: Profile, key: object) -> SessionKey | ErrorCode:
    if key is None:
        if profile is Profile.DIFFABLE:
            return ErrorCode.E_PROFILE_KEY
        return SessionKey.generate()
    if type(key) is not SessionKey:
        return ErrorCode.E_PROFILE_KEY
    return key


def _kind(source: str | os.PathLike[str]) -> str | ErrorCode:
    """The sanitizer kind of a source by its suffix. Anything else is refused."""
    try:
        raw: object = os.fspath(source)
    except TypeError:
        return ErrorCode.E_SOURCE_UNSUPPORTED
    if not isinstance(raw, str):
        return ErrorCode.E_SOURCE_UNSUPPORTED
    kind = _KINDS.get(Path(raw).suffix.lower())
    return ErrorCode.E_SOURCE_UNSUPPORTED if kind is None else kind


def _read_file(source: str | os.PathLike[str]) -> str | ErrorCode:
    """The text of a source file, read with the same checks as ingest.read_path. Code is decoded
    by the code ingest, which refuses any encoding but UTF-8."""
    try:
        raw: object = os.fspath(source)
    except TypeError:
        return ErrorCode.E_SOURCE_UNSUPPORTED
    if not isinstance(raw, str):
        return ErrorCode.E_SOURCE_UNSUPPORTED
    path = Path(raw)
    kind = _KINDS.get(path.suffix.lower())
    if kind is None:
        return ErrorCode.E_SOURCE_UNSUPPORTED
    is_code = path.suffix.lower() in _CODE_SUFFIXES
    is_log = kind == "log"
    limit = MAX_CODE_BYTES if is_code else MAX_LOG_BYTES if is_log else MAX_BYTES
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except (OSError, ValueError):
        return ErrorCode.E_SOURCE_READ
    if len(data) > limit:
        if is_code:
            return ErrorCode.E_CODE_TOO_LARGE
        return ErrorCode.E_LOG_TOO_LARGE if is_log else ErrorCode.E_SOURCE_SIZE
    if is_code:
        return decode_code(data)
    if is_log:
        return ingest_log.decoded(data)
    try:
        return data.decode(_ENCODING)
    except UnicodeDecodeError:
        return ErrorCode.E_SOURCE_DECODE


def _file_text(source: str | os.PathLike[str]) -> str:
    outcome = _read_file(source)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def _isolated(
    text: str, kind: str, profile: Profile, key: SessionKey, name: str | None = None
) -> DocV1 | TextV1 | MdV1 | CodeV1 | LogV1 | DbV1:
    model, _pairs = run_job(kind, key, profile, text, name=name)
    if not isinstance(model, DocV1 | TextV1 | MdV1 | CodeV1 | LogV1 | DbV1):
        raise AsfbError(ErrorCode.E_WORKER_PROTOCOL)
    return model


def _database_model(path: str, profile: Profile, key: SessionKey) -> DbV1:
    """A SQLite file, read in a worker always: the header and size are checked by the client."""
    found = ingest_db.check_file(path)
    model, _pairs = run_job("db", key, profile, ingest_db.encode_bytes(found.data))
    if not isinstance(model, DbV1):
        raise AsfbError(ErrorCode.E_WORKER_PROTOCOL)
    return with_wal(model, found.wal_present)


def _text_model(text: str, profile: Profile, key: SessionKey) -> TextV1:
    eol, lines = ingest.split_lines(text)
    model, _pairs = censor_text_with_pairs(key, eol, lines, profile)
    return model


def _markdown_model(text: str, profile: Profile, key: SessionKey) -> MdV1:
    model, _pairs = censor_md_with_pairs(key, parse_markdown(text), profile)
    return model


def _code_model(text: str, name: str | None, profile: Profile, key: SessionKey) -> CodeV1:
    census = censor_code(key, read_code(text), profile, name)
    return census.model


def _log_model(text: str, name: str | None, profile: Profile, key: SessionKey) -> LogV1:
    fmt = ingest_log.format_of(name)
    if fmt is None:
        raise AsfbError(ErrorCode.E_SOURCE_UNSUPPORTED)
    model, _pairs = censor_log_with_pairs(key, ingest_log.read_text(text, fmt), profile)
    return model


def _finish(model: DocV1 | TextV1 | MdV1 | CodeV1 | LogV1 | DbV1, profile: Profile) -> Doc:
    emitted = emit_before_render(model, vocab=registry())
    checked = type(model).model_validate_json(emitted, strict=True)
    return Doc(json=emitted, model=checked, profile=profile, views=Views(render(emitted)))


def _run(
    build: Callable[[Profile, SessionKey], DocV1 | TextV1 | MdV1 | CodeV1 | LogV1 | DbV1],
    profile: object,
    key: object,
) -> Doc | ErrorCode:
    try:
        chosen = _profile(profile)
        if isinstance(chosen, ErrorCode):
            return chosen
        session = _session(chosen, key)
        if isinstance(session, ErrorCode):
            return session
        return _finish(build(chosen, session), chosen)
    except AsfbError as error:
        return error.code
    except Exception:  # noqa: BLE001
        return ErrorCode.E_INTERNAL


def sanitize(
    source: str | os.PathLike[str],
    /,
    *,
    profile: Literal["strict", "diffable"] = "strict",
    key: SessionKey | None = None,
    isolate: bool = False,
) -> Doc:
    """Censor an .html, .htm, .txt, .md, .markdown, .py, .pyi, .log, .jsonl, .ndjson, .db,
    .sqlite or .sqlite3 file. A database is always read in a worker, whatever isolate says.

    Raises AsfbError with a static code.
    """
    kind = _kind(source)
    if isinstance(kind, ErrorCode):
        raise AsfbError(kind)
    name = os.path.basename(os.fspath(source))
    if kind == "db":
        path = os.fspath(source)  # _kind accepted it, so this is a str path
        outcome = _run(lambda chosen, session: _database_model(path, chosen, session), profile, key)
    elif isolate:
        outcome = _run(
            lambda chosen, session: _isolated(
                _file_text(source), kind, chosen, session, name if kind in ("code", "log") else None
            ),
            profile,
            key,
        )
    elif kind == "code":
        outcome = _run(
            lambda chosen, session: _code_model(_file_text(source), name, chosen, session),
            profile,
            key,
        )
    elif kind == "log":
        outcome = _run(
            lambda chosen, session: _log_model(_file_text(source), name, chosen, session),
            profile,
            key,
        )
    elif kind == "text":
        outcome = _run(
            lambda chosen, session: _text_model(_file_text(source), chosen, session),
            profile,
            key,
        )
    elif kind == "md":
        outcome = _run(
            lambda chosen, session: _markdown_model(_file_text(source), chosen, session),
            profile,
            key,
        )
    else:
        outcome = _run(
            lambda chosen, session: censor_document(session, ingest.read_path(source), chosen),
            profile,
            key,
        )
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def sanitize_html(
    html: str,
    /,
    *,
    profile: Literal["strict", "diffable"] = "strict",
    key: SessionKey | None = None,
    isolate: bool = False,
) -> Doc:
    """Censor an HTML string. Raises AsfbError with a static code."""
    if isolate:
        outcome = _run(
            lambda chosen, session: _isolated(html, "html", chosen, session), profile, key
        )
    else:
        outcome = _run(
            lambda chosen, session: censor_document(session, ingest.read_text(html), chosen),
            profile,
            key,
        )
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def sanitize_text(
    text: str,
    /,
    *,
    profile: Literal["strict", "diffable"] = "strict",
    key: SessionKey | None = None,
    isolate: bool = False,
) -> Doc:
    """Censor a plain text string. Raises AsfbError with a static code."""
    if isolate:
        outcome = _run(
            lambda chosen, session: _isolated(text, "text", chosen, session), profile, key
        )
    else:
        outcome = _run(lambda chosen, session: _text_model(text, chosen, session), profile, key)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def sanitize_python(
    text: str,
    /,
    *,
    profile: Literal["strict", "diffable"] = "strict",
    key: SessionKey | None = None,
    isolate: bool = False,
) -> Doc:
    """Censor a Python source string. Raises AsfbError with a static code."""
    if isolate:
        outcome = _run(
            lambda chosen, session: _isolated(text, "code", chosen, session), profile, key
        )
    else:
        outcome = _run(
            lambda chosen, session: _code_model(text, None, chosen, session), profile, key
        )
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def sanitize_markdown(
    text: str,
    /,
    *,
    profile: Literal["strict", "diffable"] = "strict",
    key: SessionKey | None = None,
    isolate: bool = False,
) -> Doc:
    """Censor a markdown string. Raises AsfbError with a static code."""
    if isolate:
        outcome = _run(lambda chosen, session: _isolated(text, "md", chosen, session), profile, key)
    else:
        outcome = _run(lambda chosen, session: _markdown_model(text, chosen, session), profile, key)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome
