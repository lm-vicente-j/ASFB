"""Session facade: one key, one vault, the roots and the profile for the session's lifetime.

`sanitize(target)` takes a document id (`d_...` or `d_...@N`), `.` for the first root, or a
root-relative path whose segments may be tokens. A file yields a Sanitized result (DocRef, emitted
JSON, CensoredView). A directory yields a Listing (ListV1 and its view) and sanitizes nothing.
Every path read goes through resolve_in_roots. Results hold only emitted JSON, views, DocRefs and
static codes: never originals or paths.

With isolate=True the parse and sanitize step runs in a worker (asfb.sandbox); otherwise it runs
in process. Either way the verified (token, surface) pairs go only into the vault.
`edit(ref, old, new)` replaces one occurrence of a surface pattern in a stored file, censors the
whole result before writing, and writes atomically only if the file is unchanged since it was read.
`diff(a, b)` compares two stored revisions line by line in their censored views (lexemes, records
and schema lines for the kinds that have them). A database file is always read in a worker.
Inner helpers return codes; the public methods raise AsfbError outside any except block.
"""

import codecs
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, final

from asfb import ingest, normalize
from asfb.editing import (
    CODE_TOKEN_RE,
    HTML_UNSAFE,
    IDENTIFIER_RE,
    MAX_NEW_NAME,
    adapt_newlines,
    check_code_text,
    check_text,
    choose,
    substitute,
    tokens_in,
)
from asfb.egress import (
    CensoredView,
    Emitted,
    code_units,
    db_units,
    emit,
    emit_before_render,
    log_units,
    render,
    render_diff,
)
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import db as ingest_db
from asfb.ingest import log as ingest_log
from asfb.ingest.code import decoded as decode_code
from asfb.ingest.code import read_text as read_code
from asfb.ingest.junit import MAX_JUNIT_BYTES, read_junit
from asfb.ingest.limits import MAX_BYTES, MAX_CODE_BYTES, MAX_LOG_BYTES
from asfb.ingest.markdown import parse_markdown
from asfb.runner import netns
from asfb.runner.execute import execute
from asfb.runner.tools import TOOLS, ToolSpec
from asfb.sandbox.client import run_job, run_job_spans
from asfb.sanitize.code import censor_code
from asfb.sanitize.db import with_wal
from asfb.sanitize.html import censor_with_pairs
from asfb.sanitize.junit import censor_junit
from asfb.sanitize.log import censor_log_with_pairs
from asfb.sanitize.markdown import censor_md_with_pairs
from asfb.sanitize.pairs import SurfacePairs
from asfb.sanitize.text import censor_text_with_pairs
from asfb.schema.code import CodeV1
from asfb.schema.db import DbV1
from asfb.schema.diff import MAX_HUNKS, DiffV1, Hunk, RefV1
from asfb.schema.doc import DocV1, Profile, VocabRef
from asfb.schema.list import MAX_PATH, ListEntry, ListExt, ListV1
from asfb.schema.log import LogV1
from asfb.schema.md import MdV1
from asfb.schema.run import Case, JunitReport, RunV1
from asfb.schema.text import TextV1
from asfb.types.docid import DocId
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.values import EnumValue
from asfb.vault import DocRef, Vault, paths
from asfb.vault.store import Stored
from asfb.vocab import RUN_IDS, FrozenVocabulary, registry

_PROFILES: Final = {member.value: member for member in Profile}
_DOC_TARGET: Final = re.compile(r"(d_[0-9a-f]{16})(?:@([1-9][0-9]{0,5}))?")
_TOKEN_SEGMENT: Final = re.compile(
    r"(t_[0-9a-f]{16})(\.(?i:html|htm|txt|md|markdown|pyi|py|log|jsonl|ndjson|db|sqlite|sqlite3))?"
)
_SEPARATORS: Final = re.compile(r"[\\/]")
_ENCODING: Final = "utf-8-sig"
_DOC_REF: Final = re.compile(r"(d_[0-9a-f]{16})@([1-9][0-9]{0,5})")
_OPS: Final[dict[str, Literal["insert", "delete", "replace"]]] = {
    "insert": "insert",
    "delete": "delete",
    "replace": "replace",
}
# The sanitizer kind of each document suffix. Keys match paths.DOCUMENT_SUFFIXES.
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
_EXTENSIONS: Final[dict[str, ListExt]] = {
    ".html": "html",
    ".htm": "htm",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "markdown",
    ".py": "py",
    ".pyi": "pyi",
    ".log": "log",
    ".jsonl": "jsonl",
    ".ndjson": "ndjson",
    ".db": "db",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite3",
}
_CODE_SCHEMA: Final = "asfb.code/1"
_LOG_SCHEMA: Final = "asfb.log/1"
_DB_SCHEMA: Final = "asfb.db/1"
_STRUCTURAL: Final = frozenset({"newline", "indent", "dedent"})
_RUN_TIMEOUT_MAX: Final = 3600
_RUN_TARGETS_MAX: Final = 32
_RUN_NETWORK_MODES: Final = ("isolate", "host")


@dataclass(frozen=True, slots=True, eq=False)
class Sanitized:
    """A censored file: its revision, the emitted JSON and the censored view."""

    ref: DocRef
    json: Emitted
    view: CensoredView


@dataclass(frozen=True, slots=True, eq=False)
class FileFailure:
    """A file of a listing that cannot be read through the roots, with its static code."""

    doc: DocId
    code: ErrorCode


@dataclass(frozen=True, slots=True, eq=False)
class Listing:
    """The documents under a directory (ListV1), their view, and the files that failed."""

    json: Emitted
    view: CensoredView
    failed: tuple[FileFailure, ...]


@dataclass(frozen=True, slots=True, eq=False)
class RunResult:
    """A test run: its emitted summary (RunV1) and the censored view of it."""

    json: Emitted
    view: CensoredView


@dataclass(frozen=True, slots=True, eq=False)
class Diffed:
    """A diff of two stored revisions: its emitted JSON (DiffV1) and the unified view."""

    json: Emitted
    view: CensoredView


def _read(path: str) -> bytes | ErrorCode:
    # A Python source and a log have their own bounds and refuse with their own codes.
    kind = _KINDS.get(Path(path).suffix.lower())
    limit = {"code": MAX_CODE_BYTES, "log": MAX_LOG_BYTES}.get(kind or "", MAX_BYTES)
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except (OSError, ValueError):
        return ErrorCode.E_SOURCE_READ
    if len(data) > limit:
        if kind == "code":
            return ErrorCode.E_CODE_TOO_LARGE
        return ErrorCode.E_LOG_TOO_LARGE if kind == "log" else ErrorCode.E_SOURCE_SIZE
    return data


def _decode(data: bytes, kind: str) -> str | ErrorCode:
    if kind == "code":
        return decode_code(data)
    if kind == "log":
        return ingest_log.decoded(data)
    try:
        return data.decode(_ENCODING)
    except UnicodeDecodeError:
        return ErrorCode.E_SOURCE_DECODE


def _view_lines(view: str) -> list[str]:
    return view.split("\n") if view else []


def _parts(path: str, start: str) -> list[str]:
    relative = os.path.relpath(path, start)
    return [] if relative == os.curdir else relative.split(os.sep)


def _plain_name(name: str) -> bool:
    return (
        name not in ("", os.curdir, os.pardir) and ":" not in name and not _SEPARATORS.search(name)
    )


def _kind_of(path: str) -> str | None:
    """The sanitizer kind of a document path by its suffix, or None for any other file."""
    return _KINDS.get(os.path.splitext(path)[1].lower())


def _stem_ext(path: str) -> tuple[str, ListExt]:
    stem, suffix = os.path.splitext(os.path.basename(path))
    return stem, _EXTENSIONS[suffix.lower()]


def _vocab_terms(emitted: str) -> frozenset[str]:
    """The terms of the vocabularies a stored document declares: the public words for edit."""
    terms: set[str] = set()
    for ref in json.loads(emitted).get("vocab", []):
        entry = registry()[ref["id"]]
        if isinstance(entry, FrozenVocabulary):
            terms |= entry.terms
    return frozenset(terms)


def _report_text(path: str) -> str | None:
    """The junit report as UTF-8 text, or None when it is missing, too large or not UTF-8."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_JUNIT_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_JUNIT_BYTES:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _run_timeout_ok(value: object) -> bool:
    return type(value) is int and 1 <= value <= _RUN_TIMEOUT_MAX


def _run_network_ok(value: object) -> bool:
    return type(value) is str and value in _RUN_NETWORK_MODES


def _run_vocab() -> tuple[VocabRef, ...]:
    """Both run vocabularies are always declared, so every run result has the same vocab list."""
    refs: list[VocabRef] = []
    for vocab_id in RUN_IDS:
        entry = registry()[vocab_id]
        refs.append(VocabRef(id=entry.id, version=entry.version))
    return tuple(refs)


def _schema_of(emitted: Emitted) -> object:
    parsed: object = json.loads(emitted).get("schema_id")
    return parsed


def _lexeme_keys(model: CodeV1) -> list[tuple[int, tuple[str, str]]]:
    """(lexeme index, key) of each significant lexeme: its kind and its censored value."""
    keys: list[tuple[int, tuple[str, str]]] = []
    for index, lexeme in enumerate(model.lexemes):
        if lexeme.kind in _STRUCTURAL:
            continue
        value = lexeme.value
        text = value.term if isinstance(value, EnumValue) else str(value)
        keys.append((index, (lexeme.kind, text)))
    return keys


def _encode(text: str, prefix: bytes) -> bytes | ErrorCode:
    try:
        return prefix + text.encode("utf-8")
    except UnicodeEncodeError:
        return ErrorCode.E_EDIT_INVALID_TEXT


def _discard(path: str) -> None:
    with suppress(OSError):
        os.remove(path)


def _replace(path: str, data: bytes, digest: bytes) -> ErrorCode | None:
    """Replace the file atomically, but only while it still has the digest the edit read."""
    try:
        handle, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".asfb-")
    except OSError:
        return ErrorCode.E_EDIT_WRITE
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        shutil.copymode(path, tmp)
        current = _read(path)
        if isinstance(current, ErrorCode) or hashlib.sha256(current).digest() != digest:
            _discard(tmp)
            return ErrorCode.E_EDIT_STALE
        os.replace(tmp, path)
    except OSError:
        _discard(tmp)
        return ErrorCode.E_EDIT_WRITE
    return None


@final
class Session:
    """Roots, profile, key and vault for one session. Holds originals only inside the vault."""

    def __init__(
        self,
        roots: Iterable[str | os.PathLike[str]],
        profile: str = "strict",
        key: SessionKey | None = None,
        python: str | None = None,
        run_timeout: int = 300,
        isolate: bool = True,
        run_network: Literal["isolate", "host"] = "isolate",
    ) -> None:
        checked = paths.check_roots([os.fspath(root) for root in roots])
        if isinstance(checked, ErrorCode):
            raise AsfbError(checked)
        chosen = _PROFILES.get(profile) if type(profile) is str else None
        if chosen is None:
            raise AsfbError(ErrorCode.E_PROFILE)
        if key is None:
            if chosen is Profile.DIFFABLE:
                raise AsfbError(ErrorCode.E_PROFILE_KEY)
            key = SessionKey.generate()
        if type(key) is not SessionKey:
            raise AsfbError(ErrorCode.E_PROFILE_KEY)
        self._roots: tuple[str, ...] = checked
        self._profile = chosen
        self._session_key = key
        self._vault = Vault(key)
        self._python = sys.executable if python is None else python
        if not _run_timeout_ok(run_timeout) or not _run_network_ok(run_network):
            raise AsfbError(ErrorCode.E_RUN_CONFIG)
        self._run_timeout = run_timeout
        self._run_network = run_network
        self._isolate = isolate

    def __repr__(self) -> str:
        return "Session(<redacted>)"

    def sanitize(self, target: str, /) -> Sanitized | Listing:
        """Censor a file or list a directory. Raises AsfbError with a static code."""
        if type(target) is not str:
            raise AsfbError(ErrorCode.E_SOURCE_UNSUPPORTED)
        found = _DOC_TARGET.fullmatch(target)
        if found is not None:
            return self._by_doc(found)
        if target == ".":
            return self._listing(self._roots[0])
        path = self._locate(target)
        if os.path.isdir(path):
            return self._listing(path)
        return self._file(path)

    def _inside(self, path: str) -> str:
        resolved = paths.resolve_in_roots(self._roots, path)
        if isinstance(resolved, ErrorCode):
            raise AsfbError(resolved)
        return resolved

    def _by_doc(self, found: re.Match[str]) -> Sanitized:
        doc = DocId(found.group(1))
        if found.group(2) is not None:
            stored = self._vault.get(doc, int(found.group(2)))
            return Sanitized(stored.ref, stored.json, stored.view)
        stored = self._vault.get(doc)
        return self._file(self._inside(stored.path))

    def _walk(self, root: str, parts: list[str]) -> str | None:
        """The path the segments name below one root, or None when it does not exist there.

        A token segment is translated only through the path scope. One surface is used as is;
        several surfaces are narrowed to the entries that exist here, and more than one of those
        is E_TOKEN_AMBIGUOUS.
        """
        current = root
        for part in parts:
            found = _TOKEN_SEGMENT.fullmatch(part)
            if found is None:
                current = os.path.join(current, part)
                continue
            ext = found.group(2) or ""
            surfaces = self._vault.path_surfaces(Token(found.group(1)))
            if not surfaces:
                raise AsfbError(ErrorCode.E_PATH_UNKNOWN_TOKEN)
            if len(surfaces) == 1:
                (name,) = surfaces
                if not _plain_name(name):
                    raise AsfbError(ErrorCode.E_PATH_OUTSIDE_ROOT)
                current = os.path.join(current, name + ext)
                continue
            present = [
                name
                for name in sorted(surfaces)
                if _plain_name(name) and os.path.lexists(os.path.join(current, name + ext))
            ]
            if not present:
                return None
            if len(present) > 1:
                raise AsfbError(ErrorCode.E_TOKEN_AMBIGUOUS)
            current = os.path.join(current, present[0] + ext)
        return current if os.path.exists(current) else None

    def _locate(self, target: str) -> str:
        if os.path.isabs(target):
            return self._inside(target)
        parts = _SEPARATORS.split(target)
        if os.path.splitdrive(target)[0] or any(
            part in ("", os.curdir, os.pardir) or ":" in part for part in parts
        ):
            raise AsfbError(ErrorCode.E_PATH_OUTSIDE_ROOT)
        for root in self._roots:
            candidate = self._walk(root, parts)
            if candidate is not None:
                return self._inside(candidate)
        raise AsfbError(ErrorCode.E_SOURCE_READ)

    def _run_targets(self, targets: object) -> tuple[str, tuple[str, ...]]:
        """The one root that contains every target, and the resolved target paths in order."""
        if not isinstance(targets, (tuple, list)) or not 1 <= len(targets) <= _RUN_TARGETS_MAX:
            raise AsfbError(ErrorCode.E_RUN_TARGETS)
        resolved: list[str] = []
        roots: set[str] = set()
        for target in targets:
            if type(target) is not str:
                raise AsfbError(ErrorCode.E_RUN_TARGETS)
            path = self._roots[0] if target == "." else self._locate(target)
            root = paths.containing_root(self._roots, path)
            if root is None:
                raise AsfbError(ErrorCode.E_PATH_OUTSIDE_ROOT)
            roots.add(root)
            resolved.append(path)
        if len(roots) != 1:
            raise AsfbError(ErrorCode.E_RUN_TARGETS)
        return roots.pop(), tuple(resolved)

    def _network_prefix(self) -> tuple[str, ...]:
        if self._run_network == "host":
            return ()
        prefix = netns.isolate_prefix()
        if prefix is None:
            raise AsfbError(ErrorCode.E_RUN_NETWORK)
        return prefix

    def _parsed(self, report: str) -> tuple[JunitReport, SurfacePairs] | None:
        """The censored junit report, or None when the file is missing, too large, not UTF-8,
        not a valid report or the isolated worker fails. Nothing partial is ever returned."""
        text = _report_text(report)
        if text is None:
            return None
        try:
            if self._isolate:
                model, pairs = run_job("junit", self._session_key, self._profile, text)
                return (model, pairs) if isinstance(model, JunitReport) else None
            return censor_junit(self._session_key, read_junit(text))
        except AsfbError:
            return None

    def _censor(
        self, kind: str, text: str, name: str | None
    ) -> tuple[DocV1 | TextV1 | MdV1 | CodeV1 | LogV1, SurfacePairs]:
        if self._isolate:
            model, pairs = run_job(kind, self._session_key, self._profile, text, name=name)
            if not isinstance(model, DocV1 | TextV1 | MdV1 | CodeV1 | LogV1):
                raise AsfbError(ErrorCode.E_WORKER_PROTOCOL)
            return model, pairs
        if kind == "code":
            census = censor_code(self._session_key, read_code(text), self._profile, name)
            return census.model, census.pairs
        if kind == "log":
            fmt = ingest_log.format_of(name)
            if fmt is None:
                raise AsfbError(ErrorCode.E_SOURCE_UNSUPPORTED)
            source = ingest_log.read_text(text, fmt)
            return censor_log_with_pairs(self._session_key, source, self._profile)
        if kind == "text":
            eol, lines = ingest.split_lines(text)
            return censor_text_with_pairs(self._session_key, eol, lines, self._profile)
        if kind == "md":
            return censor_md_with_pairs(self._session_key, parse_markdown(text), self._profile)
        return censor_with_pairs(self._session_key, ingest.read_text(text), self._profile)

    def _censored(
        self, kind: str, text: str, name: str
    ) -> tuple[Emitted, CensoredView, SurfacePairs]:
        # The file name only matters to code (its kind) and to logs (their format).
        model, pairs = self._censor(kind, text, name if kind in ("code", "log") else None)
        emitted = emit_before_render(model, vocab=registry())
        return emitted, render(emitted), pairs

    def _keep(
        self,
        resolved: str,
        doc: DocId,
        digest: bytes,
        emitted: Emitted,
        view: CensoredView,
        pairs: SurfacePairs,
    ) -> Sanitized:
        self._vault.record(pairs, doc=doc)
        ref = self._vault.put_document(resolved, self._profile, digest, emitted, view)
        return Sanitized(ref, emitted, view)

    def _censor_database(self, found: ingest_db.DbFile) -> tuple[DbV1, SurfacePairs]:
        """The database model, read in a worker. Its WAL flag is the one the client observed."""
        model, pairs = run_job(
            "db", self._session_key, self._profile, ingest_db.encode_bytes(found.data)
        )
        if not isinstance(model, DbV1):
            raise AsfbError(ErrorCode.E_WORKER_PROTOCOL)
        return with_wal(model, found.wal_present), pairs

    def _database(self, resolved: str) -> Sanitized:
        """A SQLite file: header and size are checked before its bytes are read, and the bytes go
        to a worker whatever isolate says (a hostile file is never parsed in this process). The
        digest covers the WAL flag, so a WAL file that appears starts a new revision."""
        found = ingest_db.check_file(resolved)
        marker = b"\x01" if found.wal_present else b"\x00"
        digest = hashlib.sha256(found.data + marker).digest()
        doc = self._vault.doc_id(resolved)
        latest = self._vault.find(doc)
        if latest is not None and latest.digest == digest:
            return Sanitized(latest.ref, latest.json, latest.view)
        model, pairs = self._censor_database(found)
        emitted = emit_before_render(model, vocab=registry())
        return self._keep(resolved, doc, digest, emitted, render(emitted), pairs)

    def _file(self, path: str) -> Sanitized:
        resolved = self._inside(path)
        kind = _kind_of(resolved)
        if kind is None:
            raise AsfbError(ErrorCode.E_SOURCE_UNSUPPORTED)
        if kind == "db":
            return self._database(resolved)
        data = _read(resolved)
        if isinstance(data, ErrorCode):
            raise AsfbError(data)
        digest = hashlib.sha256(data).digest()
        doc = self._vault.doc_id(resolved)
        latest = self._vault.find(doc)
        if latest is not None and latest.digest == digest:
            return Sanitized(latest.ref, latest.json, latest.view)
        text = _decode(data, kind)
        if isinstance(text, ErrorCode):
            raise AsfbError(text)
        emitted, view, pairs = self._censored(kind, text, os.path.basename(resolved))
        return self._keep(resolved, doc, digest, emitted, view, pairs)

    def _revision(self, ref: str) -> Stored:
        """A stored revision named d_<id>@N. Anything else, or an unknown one, is E_DOC_UNKNOWN."""
        found = _DOC_REF.fullmatch(ref) if type(ref) is str else None
        if found is None:
            raise AsfbError(ErrorCode.E_DOC_UNKNOWN)
        return self._vault.get(DocId(found.group(1)), int(found.group(2)))

    def diff(self, a: str, b: str, /) -> Diffed:
        """Compare two stored revisions, which may be different documents. Two Python sources are
        compared lexeme by lexeme, two logs record by record and two databases schema line by
        schema line; anything else line by line in the censored views. Only the changed ranges
        are emitted. Raises AsfbError with a static code."""
        left = self._revision(a)
        right = self._revision(b)
        units: Literal["line", "lexeme", "record", "schema"]
        if _schema_of(left.json) == _CODE_SCHEMA and _schema_of(right.json) == _CODE_SCHEMA:
            units = "lexeme"
            left_units = list(code_units(left.json))
            right_units = list(code_units(right.json))
        elif _schema_of(left.json) == _LOG_SCHEMA and _schema_of(right.json) == _LOG_SCHEMA:
            units = "record"
            left_units = list(log_units(left.json))
            right_units = list(log_units(right.json))
        elif _schema_of(left.json) == _DB_SCHEMA and _schema_of(right.json) == _DB_SCHEMA:
            units = "schema"
            left_units = list(db_units(left.json))
            right_units = list(db_units(right.json))
        else:
            units = "line"
            left_units = _view_lines(left.view)
            right_units = _view_lines(right.view)
        operations = difflib.SequenceMatcher(
            None, left_units, right_units, autojunk=False
        ).get_opcodes()
        changed = [operation for operation in operations if operation[0] != "equal"]
        if len(changed) > MAX_HUNKS:
            raise AsfbError(ErrorCode.E_DIFF_LIMIT)
        hunks = tuple(
            Hunk(op=_OPS[tag], a_start=a_start, a_end=a_end, b_start=b_start, b_end=b_end)
            for tag, a_start, a_end, b_start, b_end in changed
        )
        model = DiffV1(
            profile=self._profile,
            vocab=(),
            units=units,
            a=RefV1(doc=left.ref.doc, revision=left.ref.revision),
            b=RefV1(doc=right.ref.doc, revision=right.ref.revision),
            hunks=hunks,
        )
        emitted = emit(model, vocab=registry())
        return Diffed(emitted, render_diff(emitted, left.json, right.json))

    def edit(self, ref: str, old: str, new: str, /) -> Sanitized:
        """Replace the one occurrence of old by new in a stored document, then store the result.

        ref is d_<id>@N and must be the latest revision. Only documents already in the vault are
        edited. A log or a database is evidence and is never edited (E_EDIT_UNSUPPORTED_KIND).
        Raises AsfbError with a static code; the file is untouched on every failure.
        """
        stored = self._revision(ref)
        if _kind_of(stored.path) in ("log", "db"):
            raise AsfbError(ErrorCode.E_EDIT_UNSUPPORTED_KIND)
        doc = stored.ref.doc
        latest = self._vault.find(doc)
        if latest is None or latest.ref != stored.ref:
            raise AsfbError(ErrorCode.E_EDIT_STALE)
        if _kind_of(stored.path) == "code":
            return self._edit_code(stored, old, new)
        terms = _vocab_terms(stored.json)
        for piece in (old, new):
            problem = check_text(piece, terms)
            if problem is not None:
                raise AsfbError(problem)
        if not old or old == new:
            raise AsfbError(ErrorCode.E_EDIT_INVALID_TEXT)
        resolved = self._inside(stored.path)
        kind = _kind_of(resolved)
        if kind is None:
            raise AsfbError(ErrorCode.E_SOURCE_UNSUPPORTED)
        data = _read(resolved)
        if isinstance(data, ErrorCode):
            raise AsfbError(data)
        if hashlib.sha256(data).digest() != stored.digest:
            raise AsfbError(ErrorCode.E_EDIT_STALE)
        text = _decode(data, kind)
        if isinstance(text, ErrorCode):
            raise AsfbError(text)
        prefix = codecs.BOM_UTF8 if data.startswith(codecs.BOM_UTF8) else b""
        adapted_old = adapt_newlines(text, old)
        adapted_new = adapt_newlines(text, new)
        options = {
            token: self._vault.surfaces_for(Token(token), doc=doc) for token in tokens_in(old)
        }
        picked = choose(text, adapted_old, options)
        if isinstance(picked, ErrorCode):
            raise AsfbError(picked)
        chosen, index = picked
        for token in tokens_in(adapted_new):
            if token not in chosen:
                surfaces = self._vault.surfaces_for(Token(token), doc=doc)
                if not surfaces:
                    raise AsfbError(ErrorCode.E_EDIT_UNKNOWN_TOKEN)
                if len(surfaces) > 1:
                    raise AsfbError(ErrorCode.E_TOKEN_AMBIGUOUS)
                (surface,) = surfaces
                chosen[token] = surface
            # HTML only: a decoded surface with markup characters would create markup.
            if kind == "html" and HTML_UNSAFE.intersection(chosen[token]):
                raise AsfbError(ErrorCode.E_EDIT_INVALID_TEXT)
        old_plain = substitute(adapted_old, chosen)
        updated = text[:index] + substitute(adapted_new, chosen) + text[index + len(old_plain) :]
        body = _encode(updated, prefix)
        if isinstance(body, ErrorCode):
            raise AsfbError(body)
        if len(body) > MAX_BYTES:
            raise AsfbError(ErrorCode.E_EDIT_TOO_LARGE)
        emitted, view, pairs = self._censored(kind, updated, os.path.basename(resolved))
        outcome = _replace(resolved, body, stored.digest)
        if outcome is not None:
            raise AsfbError(outcome)
        return self._keep(resolved, doc, hashlib.sha256(body).digest(), emitted, view, pairs)

    def _code_run(self, text: str) -> tuple[CodeV1, SurfacePairs, tuple[tuple[int, int], ...]]:
        """Censor a Python text (in the worker when isolated): model, pairs and lexeme spans."""
        if self._isolate:
            model, pairs, spans = run_job_spans("code", self._session_key, self._profile, text)
            if not isinstance(model, CodeV1):
                raise AsfbError(ErrorCode.E_WORKER_PROTOCOL)
            return model, pairs, spans
        census = censor_code(self._session_key, read_code(text), self._profile, None)
        return census.model, census.pairs, census.spans

    def _resolved(self, piece: str, doc: DocId) -> str:
        """The piece with each token replaced by its one surface in the document's scope."""
        chosen: dict[str, str] = {}
        for token in CODE_TOKEN_RE.findall(piece):
            if token in chosen:
                continue
            surfaces = self._vault.surfaces_for(Token(token), doc=doc)
            if not surfaces:
                raise AsfbError(ErrorCode.E_EDIT_UNKNOWN_TOKEN)
            if len(surfaces) > 1:
                raise AsfbError(ErrorCode.E_TOKEN_AMBIGUOUS)
            (chosen[token],) = surfaces
        return CODE_TOKEN_RE.sub(lambda match: chosen[match.group(0)], piece)

    def _edit_code(self, stored: Stored, old: str, new: str) -> Sanitized:
        """Replace one lexeme sequence of a Python file. Matching is on (kind, censored value), so
        spacing and layout of the agent's text do not matter. The replaced range is the raw text
        that the first and the last matched lexemes came from; every byte outside it, dropped
        controls and CRLF included, is written as it was read (a BOM too). The result is censored
        again as a whole."""
        for piece in (old, new):
            problem = check_code_text(piece)
            if problem is not None:
                raise AsfbError(problem)
        if not old or old == new:
            raise AsfbError(ErrorCode.E_EDIT_INVALID_TEXT)
        doc = stored.ref.doc
        resolved = self._inside(stored.path)
        data = _read(resolved)
        if isinstance(data, ErrorCode):
            raise AsfbError(data)
        if hashlib.sha256(data).digest() != stored.digest:
            raise AsfbError(ErrorCode.E_EDIT_STALE)
        text = _decode(data, "code")
        if isinstance(text, ErrorCode):
            raise AsfbError(text)
        prefix = codecs.BOM_UTF8 if data.startswith(codecs.BOM_UTF8) else b""
        normalized, raw_spans = normalize.code_spans(text)
        old_model, _old_pairs, _old_spans = self._code_run(self._resolved(old, doc))
        needle = [key for _index, key in _lexeme_keys(old_model)]
        if not needle:
            raise AsfbError(ErrorCode.E_EDIT_INVALID_TEXT)
        file_model, _file_pairs, spans = self._code_run(normalized)
        haystack = _lexeme_keys(file_model)
        keys = [key for _index, key in haystack]
        width = len(needle)
        hits = [
            position
            for position in range(len(keys) - width + 1)
            if keys[position] == needle[0] and keys[position : position + width] == needle
        ]
        if not hits:
            raise AsfbError(ErrorCode.E_EDIT_NO_MATCH)
        if len(hits) > 1:
            raise AsfbError(ErrorCode.E_EDIT_AMBIGUOUS)
        first = haystack[hits[0]][0]
        last = haystack[hits[0] + len(needle) - 1][0]
        start, end = spans[first][0], spans[last][1]
        raw_range = normalize.code_raw_range(text, raw_spans, start, end)
        if raw_range is None:
            raise AsfbError(ErrorCode.E_EDIT_CONTROL_CHARACTER)
        low, high = raw_range
        new_text, _new_dropped = normalize.code_text(self._resolved(new, doc))
        _new_model, new_pairs, _new_spans = self._code_run(new_text)
        fresh = frozenset(
            token for token, _surface in new_pairs.items() if not self._vault.known(token)
        )
        for token, surface in new_pairs.items():
            if token in fresh and IDENTIFIER_RE.fullmatch(surface) and len(surface) > MAX_NEW_NAME:
                raise AsfbError(ErrorCode.E_EDIT_INVALID_TEXT)
        updated = text[:low] + adapt_newlines(text, new_text) + text[high:]
        # The mapping is one to one, so the raw result must normalize to the expected text.
        if normalize.code_text(updated)[0] != normalized[:start] + new_text + normalized[end:]:
            raise AsfbError(ErrorCode.E_EDIT_AMBIGUOUS)
        body = _encode(updated, prefix)
        if isinstance(body, ErrorCode):
            raise AsfbError(body)
        if len(body) > MAX_CODE_BYTES:
            raise AsfbError(ErrorCode.E_CODE_TOO_LARGE)
        emitted, view, pairs = self._censored("code", updated, os.path.basename(resolved))
        outcome = _replace(resolved, body, stored.digest)
        if outcome is not None:
            raise AsfbError(outcome)
        self._vault.record(new_pairs)
        self._vault.mark_model(fresh)
        return self._keep(resolved, doc, hashlib.sha256(body).digest(), emitted, view, pairs)

    def run(self, tool: str, targets: Sequence[str] = (".",), /) -> RunResult:
        """Run one allowed test tool on targets that share a root, and summarize its junit report.

        Only the junit report is read: console output is discarded. The tool runs with the
        user's permissions; its writes are not restricted. Raises AsfbError with a static code.
        """
        spec: ToolSpec | None = TOOLS.get(tool) if type(tool) is str else None
        if spec is None:
            raise AsfbError(ErrorCode.E_RUN_TOOL)
        root, found = self._run_targets(targets)
        prefix = self._network_prefix()
        with tempfile.TemporaryDirectory(prefix="asfb-run-") as scratch:
            if paths.containing_root(self._roots, os.path.realpath(scratch)) is not None:
                raise AsfbError(ErrorCode.E_RUN_CONFIG)
            report = os.path.join(scratch, "report.xml")
            argv = [*prefix, *spec.argv(self._python, report, found)]
            finished = execute(argv, cwd=root, timeout=float(self._run_timeout))
            if isinstance(finished, ErrorCode):
                raise AsfbError(finished)
            exit_name = spec.exit_name(None if finished.timed_out else finished.returncode)
            parsed = None if finished.timed_out else self._parsed(report)
        totals = None
        cases: tuple[Case, ...] = ()
        state: Literal["parsed", "dropped"] = "dropped"
        if parsed is not None:
            model, pairs = parsed
            self._vault.record(pairs)
            totals, cases, state = model.totals, model.cases, "parsed"
        summary = RunV1(
            profile=self._profile,
            vocab=_run_vocab(),
            tool=EnumValue(vocab="run.tools", term=spec.name),
            exit=exit_name,
            report=state,
            totals=totals,
            cases=cases,
        )
        emitted = emit(summary, vocab=registry())
        return RunResult(emitted, render(emitted))

    def _listing(self, directory: str) -> Listing:
        found = paths.walk_documents(
            directory, max_depth=paths.MAX_DEPTH, max_files=paths.MAX_FILES
        )
        if isinstance(found, ErrorCode):
            raise AsfbError(found)
        root = paths.containing_root(self._roots, directory)
        if root is None:
            raise AsfbError(ErrorCode.E_PATH_OUTSIDE_ROOT)
        base = _parts(directory, root)
        rows: list[tuple[str, ListEntry | None, FileFailure | None]] = []
        for candidate in found:
            resolved = paths.resolve_in_roots(self._roots, candidate)
            if isinstance(resolved, ErrorCode):
                failure = FileFailure(self._vault.doc_id(candidate), resolved)
                rows.append((os.path.normcase(candidate), None, failure))
                continue
            stem, ext = _stem_ext(candidate)
            segments = [*base, *_parts(os.path.dirname(candidate), directory), stem]
            if len(segments) > MAX_PATH:
                raise AsfbError(ErrorCode.E_LIST_DEPTH)
            tokens = self._vault.mint_path(segments)
            entry = ListEntry(doc=self._vault.doc_id(resolved), path=tokens, ext=ext)
            rows.append((os.path.normcase(resolved), entry, None))
        rows.sort(key=lambda row: row[0])
        entries = tuple(entry for _key, entry, _failure in rows if entry is not None)
        failed = tuple(failure for _key, _entry, failure in rows if failure is not None)
        listing = ListV1(profile=self._profile, vocab=(), entries=entries)
        emitted = emit(listing, vocab=registry())
        return Listing(json=emitted, view=render(emitted), failed=failed)
