from __future__ import annotations

from typing import Protocol


class AsfbUnavailable(Exception):
    pass


class Session(Protocol):
    def censor(self, text: str) -> str: ...

    def restore(self, text: str) -> str: ...


def open_session() -> Session:
    """Open an ASFB session.

    One session = one key, so tokens in the instruction and in the files match.
    restore must reject unknown tokens (raise), so a hallucinated token fails the task.
    """
    try:
        import asfb  # noqa: F401
    except ImportError as exc:
        raise AsfbUnavailable("asfb is not installed; code support is planned for milestone M4") from exc
    # Once asfb exists, wire its API here and return a Session built on it.
    raise AsfbUnavailable("asfb is installed but tests/parity/adapter.py is not wired to its API yet")
