"""Audit hook for the isolated worker: refuses sockets, process creation, file writes and dlopen.

The hook is a Python-level control only. It is not a boundary against native code, which can
still open sockets, write files or spawn processes. Once installed a hook cannot be removed, so
tests call blocked() and never install the hook in the pytest process.
"""

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

_BLOCKED_EVENTS: Final = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.spawn",
        "os.posix_spawn",
        "os.startfile",
        "ctypes.dlopen",
        "os.remove",
        "os.rename",
        "os.replace",
        "os.mkdir",
        "os.rmdir",
        "os.chmod",
    }
)
_SOCKET_PREFIX: Final = "socket."
_WRITE_FLAGS: Final = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_WRITE_MODES: Final = frozenset("wax+")
# Refused only while a code job runs: the code lexer must never compile or execute source.
_CODE_JOB_EVENTS: Final = frozenset({"compile", "exec"})
_code_job_active = False
# A SQLite job may open only the in-memory database and may never load an extension. Its file
# arrives as bytes, so no path is needed; sqlite3 itself was not blocked before this job kind.
_DB_MEMORY: Final = ":memory:"
_DB_JOB_EVENTS: Final = frozenset({"sqlite3.load_extension"})
_db_job_active = False


class Refused(RuntimeError):
    """Raised by the hook. The message is static and names no path or argument."""

    def __init__(self) -> None:
        super().__init__("asfb sandbox: blocked by audit hook")


def _opens_for_write(args: tuple[object, ...]) -> bool:
    """The open event carries (path, mode, flags); write, append, create or update is refused."""
    if len(args) < 3:
        return False
    mode, flags = args[1], args[2]
    if isinstance(mode, str) and _WRITE_MODES.intersection(mode):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


@contextmanager
def db_job() -> Iterator[None]:
    """Allow only the in-memory SQLite connection, and no extension, for the duration of a job."""
    global _db_job_active
    _db_job_active = True
    try:
        yield
    finally:
        _db_job_active = False


@contextmanager
def code_job() -> Iterator[None]:
    """Refuse compile and exec for the duration of a code job, then lift the refusal again."""
    global _code_job_active
    _code_job_active = True
    try:
        yield
    finally:
        _code_job_active = False


def _db_refused(event: str, args: tuple[object, ...]) -> bool:
    """During a SQLite job: a connection other than the in-memory one, or any extension."""
    if event == "sqlite3.connect":
        return args[:1] != (_DB_MEMORY,)
    if event == "sqlite3.enable_load_extension":
        return len(args) < 2 or args[1] is not False
    return event in _DB_JOB_EVENTS


def blocked(event: str, args: tuple[object, ...]) -> bool:
    """True when the audit event must be refused."""
    if event.startswith(_SOCKET_PREFIX) or event in _BLOCKED_EVENTS:
        return True
    if _code_job_active and event in _CODE_JOB_EVENTS:
        return True
    if _db_job_active and _db_refused(event, args):
        return True
    return event == "open" and _opens_for_write(args)


def _hook(event: str, args: tuple[object, ...]) -> None:
    if blocked(event, args):
        raise Refused


def install() -> None:
    sys.addaudithook(_hook)
