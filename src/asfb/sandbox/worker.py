"""Isolated worker: reads one request from stdin, writes one response to stdout, then exits.

Started as `python -I -m asfb.sandbox.worker` by the client. It imports nothing from vault/, mcp/,
cli/ or runner/. main() installs the POSIX limits and the audit hook before it reads the request;
serve() does the job and is what the tests call in-process (the hook cannot be removed, so the
pytest process never installs it). stdout is reserved for the framed response: stray prints go to
stderr, which the client discards.

Job kinds: html, junit, text, md, code, log and db. Unknown kinds get E_WORKER_PROTOCOL. The code
job runs under audit.code_job(), which refuses compile and exec for its whole duration. The log job
reads its format from the file name it is given (.log, .jsonl, .ndjson). The db job runs under
audit.db_job() and receives its file as base64 text.
"""

import sys
from collections.abc import Callable
from typing import BinaryIO, Final

from asfb import ingest
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import db as ingest_db
from asfb.ingest import log as ingest_log
from asfb.ingest.code import read_text as read_code_text
from asfb.ingest.junit import read_junit
from asfb.ingest.markdown import parse_markdown
from asfb.sandbox import audit, posix, protocol
from asfb.sanitize.code import censor_code
from asfb.sanitize.db import censor_db_with_pairs
from asfb.sanitize.html import censor_with_pairs
from asfb.sanitize.junit import censor_junit
from asfb.sanitize.log import censor_log_with_pairs
from asfb.sanitize.markdown import censor_md_with_pairs
from asfb.sanitize.text import censor_text_with_pairs
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey

Handler = Callable[[protocol.Request], bytes | ErrorCode]


def _html(request: protocol.Request) -> bytes | ErrorCode:
    key = SessionKey(bytes.fromhex(request.key))
    nodes = ingest.read_text(request.text)
    doc, pairs = censor_with_pairs(key, nodes, Profile(request.profile))
    return protocol.encode_ok(doc, pairs.items())


def _junit(request: protocol.Request) -> bytes | ErrorCode:
    key = SessionKey(bytes.fromhex(request.key))
    report, pairs = censor_junit(key, read_junit(request.text))
    return protocol.encode_ok(report, pairs.items())


def _text(request: protocol.Request) -> bytes | ErrorCode:
    key = SessionKey(bytes.fromhex(request.key))
    eol, lines = ingest.split_lines(request.text)
    model, pairs = censor_text_with_pairs(key, eol, lines, Profile(request.profile))
    return protocol.encode_ok(model, pairs.items())


def _md(request: protocol.Request) -> bytes | ErrorCode:
    key = SessionKey(bytes.fromhex(request.key))
    model, pairs = censor_md_with_pairs(key, parse_markdown(request.text), Profile(request.profile))
    return protocol.encode_ok(model, pairs.items())


def _log(request: protocol.Request) -> bytes | ErrorCode:
    key = SessionKey(bytes.fromhex(request.key))
    fmt = ingest_log.format_of(request.name)
    if fmt is None:
        return ErrorCode.E_WORKER_PROTOCOL
    model, pairs = censor_log_with_pairs(
        key, ingest_log.read_text(request.text, fmt), Profile(request.profile)
    )
    return protocol.encode_ok(model, pairs.items())


def _db(request: protocol.Request) -> bytes | ErrorCode:
    """A SQLite file: the request carries its base64 bytes. The reader runs under the db audit job
    (no file connection, no extension). The WAL flag is set by the caller, not here."""
    key = SessionKey(bytes.fromhex(request.key))
    data = ingest_db.decode_bytes(request.text)
    if isinstance(data, ErrorCode):
        return data
    with audit.db_job():
        source = ingest_db.read_bytes(data)
        model, pairs = censor_db_with_pairs(key, source, Profile(request.profile))
    return protocol.encode_ok(model, pairs.items())


def _code(request: protocol.Request) -> bytes | ErrorCode:
    key = SessionKey(bytes.fromhex(request.key))
    with audit.code_job():
        census = censor_code(
            key, read_code_text(request.text), Profile(request.profile), request.name
        )
    return protocol.encode_ok(census.model, census.pairs.items(), census.spans)


_HANDLERS: Final[dict[str, Handler]] = {
    "html": _html,
    "junit": _junit,
    "text": _text,
    "md": _md,
    "code": _code,
    "log": _log,
    "db": _db,
}


def _process(data: bytes) -> bytes | ErrorCode:
    """The response body for one request. Never raises; failures become static codes."""
    try:
        body = protocol.unframe(data, protocol.MAX_REQUEST)
        if isinstance(body, ErrorCode):
            return body
        request = protocol.parse_request(body)
        if isinstance(request, ErrorCode):
            return request
        handler = _HANDLERS.get(request.kind)
        if handler is None:
            return ErrorCode.E_WORKER_PROTOCOL
        return handler(request)
    except AsfbError as error:
        return error.code
    except MemoryError:
        return ErrorCode.E_WORKER_LIMIT
    except Exception:  # noqa: BLE001
        return ErrorCode.E_INTERNAL


def respond(data: bytes) -> bytes:
    """The framed response for one framed request. Too large a reply becomes E_WORKER_LIMIT."""
    outcome = _process(data)
    if isinstance(outcome, ErrorCode):
        outcome = protocol.encode_error(outcome)
    elif len(outcome) > protocol.MAX_RESPONSE:
        outcome = protocol.encode_error(ErrorCode.E_WORKER_LIMIT)
    return protocol.frame(outcome)


def serve(stdin: BinaryIO, stdout: BinaryIO) -> None:
    stdout.write(respond(stdin.read(protocol.read_limit(protocol.MAX_REQUEST))))
    stdout.flush()


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    sys.stdout = sys.stderr
    limits = posix.load()
    audit.install()
    if limits is not None:
        posix.install(*limits)
    serve(stdin, stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
