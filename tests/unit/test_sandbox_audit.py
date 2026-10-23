"""The audit predicate, tested without installing the hook (a hook cannot be removed)."""

import os
from typing import Any

import pytest

from asfb.sandbox import audit

NOT_BLOCKED = [
    ("open", ("notes.txt", "r", os.O_RDONLY)),
    ("open", ("notes.txt", None, os.O_RDONLY)),
    ("os.getcwd", ()),
    ("os.stat", ("notes.txt",)),
    ("import", ("asfb.x", None, None)),
    ("compile", ("src", "f", "exec")),
]


@pytest.mark.parametrize(
    "event", ["socket.__new__", "socket.getaddrinfo", "socket.connect", "socket.gethostname"]
)
def test_socket_events_are_refused(event: str) -> None:
    assert audit.blocked(event, ()) is True


@pytest.mark.parametrize(
    "event",
    [
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
    ],
)
def test_process_dlopen_and_path_mutation_events_are_refused(event: str) -> None:
    assert audit.blocked(event, ("a", "b")) is True


@pytest.mark.parametrize(
    ("mode", "flags"),
    [
        ("w", os.O_WRONLY | os.O_CREAT | os.O_TRUNC),
        ("a", os.O_WRONLY | os.O_APPEND | os.O_CREAT),
        ("x", os.O_WRONLY | os.O_CREAT),
        ("r+", os.O_RDWR),
        ("r", os.O_RDWR),
        ("rb", os.O_CREAT),
        ("r", os.O_WRONLY),
    ],
)
def test_open_for_write_is_refused(mode: str, flags: int) -> None:
    assert audit.blocked("open", ("target.txt", mode, flags)) is True


@pytest.mark.parametrize(("mode", "flags"), [("r", os.O_RDONLY), ("rb", os.O_RDONLY)])
def test_open_for_read_is_allowed(mode: str, flags: int) -> None:
    assert audit.blocked("open", ("notes.txt", mode, flags)) is False


@pytest.mark.parametrize(("event", "args"), NOT_BLOCKED)
def test_other_events_are_allowed(event: str, args: tuple[Any, ...]) -> None:
    assert audit.blocked(event, args) is False


def test_open_event_with_too_few_arguments_is_allowed() -> None:
    assert audit.blocked("open", ("notes.txt",)) is False


def test_refused_message_is_static() -> None:
    error = audit.Refused()
    assert str(error) == "asfb sandbox: blocked by audit hook"
    assert "target" not in str(error)
