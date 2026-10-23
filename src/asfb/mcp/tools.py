"""The four MCP tools: sanitize, edit, run and diff. Argument checks are done here by hand.

Every answer is built from censored views, document references, static codes and literals of
this module. A failure returns its static code with isError set; nothing from the arguments is
echoed back.
"""

import re
from collections.abc import Mapping
from typing import Final

from asfb.errors import AsfbError, ErrorCode
from asfb.session import Listing, Sanitized, Session

MAX_TARGET: Final = 4096
MAX_TEXT: Final = 65_536
MAX_TARGETS: Final = 32
_REF: Final = re.compile(r"d_[0-9a-f]{16}@[1-9][0-9]{0,5}")
TOOL_NAMES: Final = ("sanitize", "edit", "run", "diff")
_TOOL_NAMES_SET: Final = frozenset(TOOL_NAMES)
_TOO_LONG: Final = 1 << 40

_REF_SCHEMA: Final = {"type": "string", "pattern": r"^d_[0-9a-f]{16}@[1-9][0-9]{0,5}$"}
_TEXT_SCHEMA: Final = {"type": "string", "maxLength": MAX_TEXT}
_TARGET_SCHEMA: Final = {"type": "string", "maxLength": MAX_TARGET}

TOOL_LIST: Final[tuple[dict[str, object], ...]] = (
    {
        "name": "sanitize",
        "description": "Censor a file under the roots, or list a directory. Returns the reference "
        "and the censored view of a file, or the view of a listing.",
        "inputSchema": {
            "type": "object",
            "properties": {"target": _TARGET_SCHEMA},
            "required": ["target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "edit",
        "description": "Replace one occurrence of old by new in a stored document (latest "
        "revision).",
        "inputSchema": {
            "type": "object",
            "properties": {"doc": _REF_SCHEMA, "old": _TEXT_SCHEMA, "new": _TEXT_SCHEMA},
            "required": ["doc", "old", "new"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run",
        "description": "Run an allowed test tool (pytest only) on targets under one root and "
        "return the summary of its junit report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "enum": ["pytest"]},
                "targets": {
                    "type": "array",
                    "items": _TARGET_SCHEMA,
                    "minItems": 1,
                    "maxItems": MAX_TARGETS,
                    "default": ["."],
                },
            },
            "required": ["tool"],
            "additionalProperties": False,
        },
    },
    {
        "name": "diff",
        "description": "Compare two stored revisions line by line in their censored views.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": _REF_SCHEMA, "b": _REF_SCHEMA},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    },
)


def _args(
    arguments: object, required: tuple[str, ...], optional: tuple[str, ...]
) -> Mapping[str, object]:
    """The arguments as a dict with exactly the expected keys, or E_MCP_ARGS."""
    if not isinstance(arguments, dict):
        raise AsfbError(ErrorCode.E_MCP_ARGS)
    allowed = set(required) | set(optional)
    if not set(required) <= set(arguments) or not set(arguments) <= allowed:
        raise AsfbError(ErrorCode.E_MCP_ARGS)
    return arguments


def _text(value: object, limit: int) -> str:
    if type(value) is not str or _utf8_size(value) > limit:
        raise AsfbError(ErrorCode.E_MCP_ARGS)
    return value


def _utf8_size(value: str) -> int:
    """Byte length of the UTF-8 form; a string that cannot be encoded counts as too long."""
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return _TOO_LONG


def _ref(value: object) -> str:
    if type(value) is not str or _REF.fullmatch(value) is None:
        raise AsfbError(ErrorCode.E_MCP_ARGS)
    return value


def _targets(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_TARGETS:
        raise AsfbError(ErrorCode.E_MCP_ARGS)
    return [_text(item, MAX_TARGET) for item in value]


def _sanitized(result: Sanitized) -> str:
    return f"{result.ref}\n{result.view}"


def _listing(result: Listing) -> str:
    lines = [result.view, *(f"{failure.doc} {failure.code.value}" for failure in result.failed)]
    return "\n".join(lines)


def _dispatch(session: Session, name: str, arguments: object) -> str:
    if name == "sanitize":
        args = _args(arguments, ("target",), ())
        result = session.sanitize(_text(args["target"], MAX_TARGET))
        return _listing(result) if isinstance(result, Listing) else _sanitized(result)
    if name == "edit":
        args = _args(arguments, ("doc", "old", "new"), ())
        edited = session.edit(
            _ref(args["doc"]), _text(args["old"], MAX_TEXT), _text(args["new"], MAX_TEXT)
        )
        return _sanitized(edited)
    if name == "run":
        args = _args(arguments, ("tool",), ("targets",))
        if args["tool"] != "pytest":
            raise AsfbError(ErrorCode.E_MCP_ARGS)
        targets = _targets(args["targets"]) if "targets" in args else ["."]
        return session.run("pytest", targets).view
    if name == "diff":
        args = _args(arguments, ("a", "b"), ())
        return session.diff(_ref(args["a"]), _ref(args["b"])).view
    raise AsfbError(ErrorCode.E_MCP_ARGS)


def call(session: Session, name: str, arguments: object) -> tuple[str, bool]:
    """Run one tool. Returns (text, is_error); the text is the view or a static code."""
    if name not in _TOOL_NAMES_SET:
        return ErrorCode.E_MCP_ARGS.value, True
    try:
        return _dispatch(session, name, arguments), False
    except AsfbError as error:
        return error.code.value, True
    except Exception:  # noqa: BLE001
        return ErrorCode.E_INTERNAL.value, True
