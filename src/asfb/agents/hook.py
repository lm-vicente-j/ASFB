"""PreToolUse hook: `python -m asfb.agents.hook --protected P [--protected P ...]`.

Claude Code sends the tool call as JSON on stdin. The hook exits 2 (and prints `asfb: blocked`)
when the call would touch a protected path, and exits 0 otherwise. Every doubt blocks: invalid or
oversized input, a missing or mistyped field, no protected path, or a bad argument list.
This is best effort; see the README for what it does not stop.
"""

import json
import os
import sys
from collections.abc import Sequence
from typing import Final

from asfb.agents.claude import rule_path

MAX_INPUT: Final = 1_048_576
_BLOCKED: Final = "asfb: blocked\n"
_FILE_TOOLS: Final = frozenset({"Read", "Edit", "MultiEdit", "Write", "NotebookEdit"})
_SEARCH_TOOLS: Final = frozenset({"Glob", "Grep"})
_SHELL_TOOLS: Final = frozenset({"Bash", "PowerShell"})
_WINDOWS: Final = os.name == "nt"


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(name: str) -> object:
    raise ValueError("non-finite number")


def _fold(text: str) -> str:
    """Case folding for text matches: Windows paths are case-insensitive, POSIX ones are not."""
    return text.casefold() if _WINDOWS else text


def _resolved(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _inside(path: str, base: str) -> bool:
    try:
        return os.path.commonpath([path, base]) == base
    except ValueError:
        return False


def _related(path: str, protected: str) -> bool:
    """Inside the protected path, or an ancestor of it (so a search would reach it)."""
    return _inside(path, protected) or _inside(protected, path)


def _forms(protected: str) -> tuple[str, ...]:
    """Textual forms of a protected path a command or pattern could contain."""
    resolved = os.path.realpath(protected)
    native = rule_path(resolved)[2:]
    forms = {resolved, resolved.replace("\\", "/"), native}
    return tuple(_fold(form) for form in forms if form)


def _mentions(text: str, forms: Sequence[str]) -> bool:
    folded = _fold(text)
    return any(form in folded for form in forms)


def _field_path(tool_input: dict[str, object], key: str, cwd: str) -> str | None:
    """The resolved path named by a field, or cwd when the field is absent. None if mistyped."""
    value = tool_input.get(key)
    if value is None:
        return _resolved(cwd)
    if type(value) is not str:
        return None
    return _resolved(value if os.path.isabs(value) else os.path.join(cwd, value))


def decide(event: object, protected: Sequence[str]) -> bool:
    """True when the tool call must be denied. Unknown tools are allowed."""
    if not isinstance(event, dict) or not protected:
        return True
    tool = event.get("tool_name")
    tool_input = event.get("tool_input")
    cwd = event.get("cwd")
    if type(tool) is not str or type(tool_input) is not dict or type(cwd) is not str:
        return True
    if not os.path.isabs(cwd):
        return True
    roots = [_resolved(path) for path in protected]
    if tool in _FILE_TOOLS:
        raw = tool_input.get("file_path")
        if raw is None:
            raw = tool_input.get("notebook_path")
        if type(raw) is not str:
            return True
        target = _resolved(raw if os.path.isabs(raw) else os.path.join(cwd, raw))
        return any(_inside(target, root) for root in roots)
    if tool in _SEARCH_TOOLS:
        base = _field_path(tool_input, "path", cwd)
        pattern = tool_input.get("pattern", "")
        if base is None or type(pattern) is not str:
            return True
        if any(_related(base, root) for root in roots):
            return True
        return any(_mentions(pattern, _forms(path)) for path in protected)
    if tool in _SHELL_TOOLS:
        command = tool_input.get("command")
        if type(command) is not str:
            return True
        here = _resolved(cwd)
        if any(_related(here, root) for root in roots):
            return True
        return any(_mentions(command, _forms(path)) for path in protected)
    return False


def _block() -> int:
    sys.stderr.write(_BLOCKED)
    return 2


def _protected(argv: Sequence[str]) -> list[str] | None:
    """The values of every --protected pair. Anything else, or none, is a refusal."""
    paths: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] != "--protected" or index + 1 >= len(argv):
            return None
        if not argv[index + 1]:
            return None
        paths.append(argv[index + 1])
        index += 2
    return paths or None


def _event(data: bytes) -> object:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        return None


def _check(argv: Sequence[str]) -> int:
    protected = _protected(argv)
    if protected is None:
        return _block()
    data = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        return _block()
    if decide(_event(data), protected):
        return _block()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 to allow the tool call, 2 to block it. Any failure blocks: Claude Code treats other
    exit codes as non-blocking, so an unexpected exception must not let the call through."""
    try:
        return _check(list(sys.argv[1:] if argv is None else argv))
    except Exception:  # noqa: BLE001
        return _block()


if __name__ == "__main__":
    raise SystemExit(main())
