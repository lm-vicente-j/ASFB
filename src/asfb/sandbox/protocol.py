"""Wire format between the main process and the isolated worker.

A frame is a 4-byte big-endian length and a UTF-8 JSON object. One request and one response per
worker. Parsing is strict: duplicate keys, NaN and unknown fields are refused, and every request
and reply is validated with a closed pydantic model. Inner functions return codes; callers raise.
"""

import json
from collections.abc import Iterable
from typing import Annotated, Final, Literal, NoReturn, get_args

from pydantic import BeforeValidator, Field, TypeAdapter, ValidationError

from asfb.errors import ErrorCode
from asfb.schema.code import CodeV1
from asfb.schema.db import DbV1
from asfb.schema.doc import DocV1
from asfb.schema.log import LogV1
from asfb.schema.md import MdV1
from asfb.schema.run import JunitReport
from asfb.schema.text import TextV1
from asfb.types.base import StrictModel
from asfb.types.token import Token

VERSION: Final = 1
HEADER: Final = 4
# A log of 16 MiB is sent as JSON text, where control characters take up to six bytes each.
MAX_REQUEST: Final = 32 * 1024 * 1024
MAX_RESPONSE: Final = 64 * 1024 * 1024
KINDS: Final = frozenset({"html", "junit", "text", "md", "code", "log", "db"})
# The model each kind replies with; the client refuses any other type for a kind.
WorkerModel = DocV1 | JunitReport | TextV1 | MdV1 | CodeV1 | LogV1 | DbV1
MAX_SPAN: Final = 1 << 21
MAX_NAME: Final = 255

# Codes the worker may return. The main process refuses any other code as a protocol error.
WorkerCode = Literal[
    "E_SOURCE_SIZE",
    "E_SOURCE_DECODE",
    "E_HTML_PARSE",
    "E_HTML_DEPTH",
    "E_HTML_NODES",
    "E_HTML_ATTRS",
    "E_TEXT_LINES",
    "E_MD_DEPTH",
    "E_MD_BLOCKS",
    "E_CODE_TOKENIZE_FAILED",
    "E_CODE_ENCODING_UNSUPPORTED",
    "E_CODE_TOO_LARGE",
    "E_CODE_LINE_TOO_LONG",
    "E_CODE_TOO_MANY_LEXEMES",
    "E_CODE_INDENT_TOO_DEEP",
    "E_LOG_TOO_LARGE",
    "E_LOG_TOO_MANY_RECORDS",
    "E_LOG_LINE_TOO_LONG",
    "E_LOG_ENCODING_UNSUPPORTED",
    "E_LOG_TOO_MANY_FIELDS",
    "E_LOG_FIELD_NAME_TOO_LONG",
    "E_DB_NOT_SQLITE",
    "E_DB_TOO_LARGE",
    "E_DB_CORRUPT",
    "E_DB_UNSUPPORTED_RUNTIME",
    "E_DB_DENIED",
    "E_DB_BUDGET_EXCEEDED",
    "E_DB_LIMIT_EXCEEDED",
    "E_VOCAB_DATA",
    "E_RUN_PARSE",
    "E_TOKEN_ENCODING",
    "E_PROFILE",
    "E_WORKER_PROTOCOL",
    "E_WORKER_LIMIT",
    "E_INTERNAL",
]
WORKER_CODES: Final = frozenset(get_args(WorkerCode))


def _exact_version(value: object) -> object:
    # Literal[1] alone also accepts the JSON boolean true; only the integer 1 passes.
    if type(value) is not int or value != VERSION:
        raise ValueError("expected version 1")
    return value


def _exact_true(value: object) -> object:
    if value is not True:
        raise ValueError("expected true")
    return value


def _exact_false(value: object) -> object:
    if value is not False:
        raise ValueError("expected false")
    return value


Version = Annotated[Literal[1], BeforeValidator(_exact_version)]


class Request(StrictModel):
    v: Version
    kind: str
    key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    profile: Literal["strict", "diffable"]
    text: str
    name: Annotated[str, Field(max_length=MAX_NAME)] | None = None


class OkReply(StrictModel):
    """A model, its pairs and, for code only, the offset span of each lexeme (in lexeme order)."""

    v: Version
    ok: Annotated[Literal[True], BeforeValidator(_exact_true)]
    doc: Annotated[WorkerModel, Field(discriminator="schema_id")]
    pairs: tuple[tuple[Token, str], ...]
    spans: tuple[
        tuple[Annotated[int, Field(ge=0, le=MAX_SPAN)], Annotated[int, Field(ge=0, le=MAX_SPAN)]],
        ...,
    ] = ()


class ErrReply(StrictModel):
    v: Version
    ok: Annotated[Literal[False], BeforeValidator(_exact_false)]
    code: WorkerCode


_REQUEST: Final = TypeAdapter(Request)
_REPLY: Final = TypeAdapter(OkReply | ErrReply)


def read_limit(limit: int) -> int:
    """How many bytes the reader takes: one frame plus one byte to detect overflow."""
    return HEADER + limit + 1


def frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(HEADER, "big") + payload


def unframe(data: bytes, limit: int) -> bytes | ErrorCode:
    """Return the payload of a complete frame. Any other shape is a protocol error."""
    if len(data) < HEADER:
        return ErrorCode.E_WORKER_PROTOCOL
    size = int.from_bytes(data[:HEADER], "big")
    if size > limit or len(data) != HEADER + size:
        return ErrorCode.E_WORKER_PROTOCOL
    return data[HEADER:]


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(name: str) -> NoReturn:
    raise ValueError("non-finite number")


def _strict_json(body: bytes) -> bool:
    try:
        json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        return False
    return True


def _dumps(payload: object) -> bytes | None:
    try:
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return text.encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        return None


def encode_request(
    kind: str, key: bytes, profile: str, text: str, name: str | None = None
) -> bytes | ErrorCode:
    """The framed request. Text that cannot be UTF-8 or does not fit the cap is refused."""
    if type(text) is not str or (name is not None and type(name) is not str):
        return ErrorCode.E_SOURCE_UNSUPPORTED
    body = _dumps(
        {
            "v": VERSION,
            "kind": kind,
            "key": key.hex(),
            "profile": profile,
            "text": text,
            "name": name,
        }
    )
    if body is None:
        return ErrorCode.E_SOURCE_DECODE
    if len(body) > MAX_REQUEST:
        return ErrorCode.E_SOURCE_SIZE
    return frame(body)


def parse_request(body: bytes) -> Request | ErrorCode:
    if not _strict_json(body):
        return ErrorCode.E_WORKER_PROTOCOL
    try:
        return _REQUEST.validate_json(body, strict=True)
    except ValidationError:
        return ErrorCode.E_WORKER_PROTOCOL


def parse_reply(body: bytes) -> OkReply | ErrReply | ErrorCode:
    if not _strict_json(body):
        return ErrorCode.E_WORKER_PROTOCOL
    try:
        return _REPLY.validate_json(body, strict=True)
    except ValidationError:
        return ErrorCode.E_WORKER_PROTOCOL


def encode_ok(
    doc: WorkerModel,
    pairs: Iterable[tuple[str, str]],
    spans: Iterable[tuple[int, int]] = (),
) -> bytes | ErrorCode:
    body = _dumps(
        {
            "v": VERSION,
            "ok": True,
            "doc": doc.model_dump(mode="json", warnings="error"),
            "pairs": [[token, surface] for token, surface in pairs],
            "spans": [[start, end] for start, end in spans],
        }
    )
    if body is None:
        return ErrorCode.E_INTERNAL
    return body


def encode_error(code: ErrorCode) -> bytes:
    body = _dumps({"v": VERSION, "ok": False, "code": code.value})
    return body if body is not None else b""
