"""Test-only entry point: applies the worker's limits and audit hook, then performs one action.

Run as `python -I -m asfb.sandbox.selftest ACTION [options]`. The worker never imports this module
and never runs it; it exists so tests can check that the limits and the hook hold. Lower limits
are passed in so the tests stay fast. On Windows the memory and CPU limits come from the Job
Object the parent creates (run_action passes them); on POSIX they come from posix.install().

Actions: alloc, spin, socket, write, spawn, crash, hang, emit. "emit" writes the given hex bytes to
stdout verbatim and is used to feed malformed responses to the client.

Exit codes: 0 action finished; 20 refused by the audit hook; 21 MemoryError. Any other nonzero
exit means the process was killed by a limit or crashed.
"""

import argparse
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from asfb.errors import ErrorCode
from asfb.sandbox import audit, posix
from asfb.sandbox.client import MIB, ChildResult, Limits, run_child

EXIT_REFUSED: Final = 20
EXIT_MEMORY: Final = 21
_CRASH_CODE: Final = 7


def _alloc(args: argparse.Namespace) -> None:
    buffer = bytearray(args.memory_mib * 4 * MIB)
    buffer[0] = 1


def _spin(args: argparse.Namespace) -> None:
    while True:
        pass


def _socket(args: argparse.Namespace) -> None:
    socket.socket()


def _write(args: argparse.Namespace) -> None:
    with Path(args.target).open("w", encoding="utf-8") as handle:
        handle.write("x")


def _spawn(args: argparse.Namespace) -> None:
    subprocess.Popen([sys.executable, "-I", "-c", "pass"])


def _crash(args: argparse.Namespace) -> None:
    os._exit(_CRASH_CODE)


def _hang(args: argparse.Namespace) -> None:
    time.sleep(600)


def _emit(args: argparse.Namespace) -> None:
    sys.stdout.buffer.write(bytes.fromhex(args.hex))
    sys.stdout.buffer.flush()


_ACTIONS: Final[dict[str, Callable[[argparse.Namespace], None]]] = {
    "alloc": _alloc,
    "spin": _spin,
    "socket": _socket,
    "write": _write,
    "spawn": _spawn,
    "crash": _crash,
    "hang": _hang,
    "emit": _emit,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asfb.sandbox.selftest")
    parser.add_argument("action", choices=sorted(_ACTIONS))
    parser.add_argument("--cpu-seconds", type=int, default=1)
    parser.add_argument("--memory-mib", type=int, default=128)
    parser.add_argument("--target", default="")
    parser.add_argument("--hex", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    loaded = posix.load()
    audit.install()
    if loaded is not None:
        resource, libc = loaded
        posix.install(
            resource,
            libc,
            cpu_seconds=args.cpu_seconds,
            address_bytes=args.memory_mib * MIB,
        )
    try:
        _ACTIONS[args.action](args)
    except audit.Refused:
        return EXIT_REFUSED
    except MemoryError:
        return EXIT_MEMORY
    return 0


def run_action(
    action: str,
    *,
    cpu_seconds: int = 30,
    memory_mib: int = 512,
    target: str = "",
    payload: bytes = b"",
    timeout: float = 20.0,
) -> ChildResult | ErrorCode:
    """Run one action in a child with the given limits and return what happened."""
    argv = [
        sys.executable,
        "-I",
        "-m",
        "asfb.sandbox.selftest",
        action,
        "--cpu-seconds",
        str(cpu_seconds),
        "--memory-mib",
        str(memory_mib),
        "--target",
        target,
        "--hex",
        payload.hex(),
    ]
    limits = Limits(memory_bytes=memory_mib * MIB, cpu_seconds=cpu_seconds, processes=2)
    return run_child(argv, b"", timeout=timeout, limits=limits)


if __name__ == "__main__":
    raise SystemExit(main())
