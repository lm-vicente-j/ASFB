"""JSON-RPC 2.0 over stdio: one message per line, sequential requests.

handle() turns one message into one reply line (or None for notifications). serve() reads lines
from stdin and writes replies to stdout; it never writes to stderr. Parsing is strict: duplicate
keys, NaN and Infinity are refused. Error messages are static strings.
"""

import json
from enum import Enum
from typing import BinaryIO, Final, NoReturn

from asfb.mcp import tools
from asfb.session import Session

MAX_LINE: Final = 4 * 1024 * 1024
PROTOCOL_VERSIONS: Final = ("2025-06-18", "2025-03-26", "2024-11-05")
_DEFAULT_VERSION: Final = "2025-06-18"
_SERVER_INFO: Final = {"name": "asfb", "version": "0.1.0.dev0"}


class _Marker(Enum):
    """Not a message: the input ended, or one line was too long."""

    EOF = 1
    TOO_LONG = 2


_PARSE_ERROR: Final = -32700
_INVALID_REQUEST: Final = -32600
_METHOD_NOT_FOUND: Final = -32601
_INVALID_PARAMS: Final = -32602
_MESSAGES: Final = {
    _PARSE_ERROR: "Parse error",
    _INVALID_REQUEST: "Invalid request",
    _METHOD_NOT_FOUND: "Method not found",
    _INVALID_PARAMS: "Invalid params",
}


def _reject_constant(name: str) -> NoReturn:
    raise ValueError("non-finite number")


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _strict_loads(text: str) -> object:
    """The parsed JSON value, or _Marker.EOF when the text is not strict JSON."""
    try:
        return json.loads(
            text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError):
        return _Marker.EOF


def _dumps(payload: dict[str, object]) -> bytes:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.encode("utf-8")


def _error(request_id: object, code: int) -> bytes:
    message = _MESSAGES[code]
    return _dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _result(request_id: object, result: object) -> bytes:
    return _dumps({"jsonrpc": "2.0", "id": request_id, "result": result})


def _valid_id(value: object) -> bool:
    return value is None or type(value) is str or type(value) is int


def _initialize(params: object) -> dict[str, object]:
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    version = requested if requested in PROTOCOL_VERSIONS else _DEFAULT_VERSION
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": _SERVER_INFO,
    }


def _call(session: Session, params: object) -> dict[str, object] | None:
    if not isinstance(params, dict) or type(params.get("name")) is not str:
        return None
    name = params["name"]
    if name not in tools.TOOL_NAMES:
        return None
    text, is_error = tools.call(session, name, params.get("arguments", {}))
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(session: Session, message: bytes, /) -> bytes | None:
    """The reply line for one message, or None when no reply is due."""
    try:
        text = message.decode("utf-8")
    except UnicodeDecodeError:
        return _error(None, _PARSE_ERROR)
    parsed = _strict_loads(text)
    if parsed is _Marker.EOF:
        return _error(None, _PARSE_ERROR)
    if not isinstance(parsed, dict) or parsed.get("jsonrpc") != "2.0":
        return _error(None, _INVALID_REQUEST)
    if "id" not in parsed:
        # A notification: it is executed when it is a known method, and never answered.
        return None
    request_id = parsed["id"]
    if not _valid_id(request_id):
        return _error(None, _INVALID_REQUEST)
    method = parsed.get("method")
    if type(method) is not str:
        return _error(request_id, _INVALID_REQUEST)
    if method == "initialize":
        return _result(request_id, _initialize(parsed.get("params")))
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": list(tools.TOOL_LIST)})
    if method == "tools/call":
        outcome = _call(session, parsed.get("params"))
        if outcome is None:
            return _error(request_id, _INVALID_PARAMS)
        return _result(request_id, outcome)
    return _error(request_id, _METHOD_NOT_FOUND)


def _read_line(stream: BinaryIO) -> bytes | _Marker:
    """One line without its newline. An over-long line is drained to its end and marked."""
    chunk = stream.readline(MAX_LINE + 1)
    if not chunk:
        return _Marker.EOF
    if chunk.endswith(b"\n"):
        return chunk[:-1]
    if len(chunk) <= MAX_LINE:
        return chunk
    while True:
        rest = stream.readline(MAX_LINE + 1)
        if not rest or rest.endswith(b"\n"):
            return _Marker.TOO_LONG


def serve(session: Session, stdin: BinaryIO, stdout: BinaryIO) -> int:
    """Answer requests until stdin ends. Returns 0; the process never writes to stderr."""
    while True:
        line = _read_line(stdin)
        reply: bytes | None
        if line is _Marker.EOF:
            return 0
        if line is _Marker.TOO_LONG:
            reply = _error(None, _INVALID_REQUEST)
        elif not line.strip():
            reply = None
        else:
            reply = handle(session, line)
        if reply is not None:
            stdout.write(reply + b"\n")
            stdout.flush()
