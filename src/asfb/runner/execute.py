"""Start one test tool and wait for it under a wall-clock limit. The whole process tree is killed.

Output is discarded: the only result is the report file the caller reads. POSIX: the child leads
its own session and group, and the group is killed with SIGKILL. Windows: the child starts
suspended and goes into a Job Object (kill on close, memory, CPU and process limits), and closing
the job kills the tree. Inner helpers return codes; execute returns Finished or E_RUN_START.
"""

import math
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Final, Protocol

from asfb.errors import ErrorCode

_MEMORY_BYTES: Final = 4 * 1024 * 1024 * 1024
_PROCESSES: Final = 64
_REAP_SECONDS: Final = 10.0


class _Closable(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Finished:
    """How the child ended: its return code, or timed_out with no return code."""

    returncode: int | None
    timed_out: bool


def _popen(argv: Sequence[str], cwd: str) -> subprocess.Popen[bytes]:
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        # Imported only on Windows: the module calls ctypes.WinDLL, which does not exist elsewhere.
        from asfb.sandbox import windows

        options["creationflags"] = windows.CREATE_SUSPENDED
    else:
        options["start_new_session"] = True
    return subprocess.Popen(  # noqa: S603
        list(argv),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
        **options,
    )


def _attach(pid: int, timeout: float) -> _Closable | None:
    job: _Closable | None = None
    if sys.platform == "win32":
        from asfb.sandbox import windows

        job = windows.attach(
            pid,
            memory_bytes=_MEMORY_BYTES,
            cpu_seconds=math.ceil(timeout),
            processes=_PROCESSES,
        )
    return job


def _wait(proc: subprocess.Popen[bytes], timeout: float) -> Finished:
    try:
        return Finished(returncode=proc.wait(timeout=timeout), timed_out=False)
    except subprocess.TimeoutExpired:
        return Finished(returncode=None, timed_out=True)


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill what is left of the child: its POSIX group, then the child; always reap it."""
    if sys.platform != "win32":
        with suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with suppress(OSError):
        proc.kill()
    with suppress(OSError, subprocess.TimeoutExpired):
        proc.wait(timeout=_REAP_SECONDS)


def execute(argv: Sequence[str], *, cwd: str, timeout: float) -> Finished | ErrorCode:
    """Run argv in cwd with no input and no output, and wait at most timeout seconds."""
    try:
        proc = _popen(argv, cwd)
    except OSError:
        return ErrorCode.E_RUN_START
    job: _Closable | None = None
    try:
        if sys.platform == "win32":
            job = _attach(proc.pid, timeout)
            if job is None:
                return ErrorCode.E_RUN_START
        return _wait(proc, timeout)
    finally:
        _kill_tree(proc)
        if job is not None:
            job.close()
