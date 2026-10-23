"""Main-side client for the isolated worker: spawn, limits, wall-clock timeout, framing, pair check.

One worker per job, started as `python -I -m asfb.sandbox.worker` in a fresh empty directory with
a minimal environment. Its stderr is discarded. On Windows the child is created suspended, put in
a Job Object and resumed. The client does not call egress; the caller does.

Inner helpers return codes; the public function raises AsfbError outside any except block.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import IO, Any, Final, Protocol

from asfb.errors import AsfbError, ErrorCode
from asfb.sandbox import protocol
from asfb.sanitize.pairs import SurfacePairs
from asfb.sanitize.tokens import token_for
from asfb.schema.code import CodeV1
from asfb.schema.db import DbV1
from asfb.schema.doc import DocV1, Profile
from asfb.schema.log import LogV1
from asfb.schema.md import MdV1
from asfb.schema.run import JunitReport
from asfb.schema.text import TextV1
from asfb.types.key import SessionKey
from asfb.types.token import SHAPE_RE, Token
from asfb.types.untrusted import Untrusted

MIB: Final = 1024 * 1024
_EXPECTED: Final[
    dict[
        str,
        type[DocV1]
        | type[JunitReport]
        | type[TextV1]
        | type[MdV1]
        | type[CodeV1]
        | type[LogV1]
        | type[DbV1],
    ]
] = {
    "html": DocV1,
    "junit": JunitReport,
    "text": TextV1,
    "md": MdV1,
    "code": CodeV1,
    "log": LogV1,
    "db": DbV1,
}
Outcome = tuple[protocol.WorkerModel, SurfacePairs, tuple[tuple[int, int], ...]]
WALL_SECONDS: Final = 60.0
# Per kind. A code job is one tokenizer pass and one censor; a worker still running after 15 s is
# killed and its document refused (E_WORKER_TIMEOUT). Every other kind keeps WALL_SECONDS.
KIND_WALL_SECONDS: Final[Mapping[str, float]] = {"code": 15.0}
WORKER_ARGV: Final = (sys.executable, "-I", "-m", "asfb.sandbox.worker")
_ENV_KEYS: Final = ("SYSTEMROOT", "PATH", "TEMP", "TMP")
_JOIN_SECONDS: Final = 5.0
_REAP_SECONDS: Final = 10.0


@dataclass(frozen=True, slots=True)
class Limits:
    """Main-side limits, applied through a Job Object on Windows. POSIX limits are in the worker."""

    memory_bytes: int = 512 * MIB
    cpu_seconds: int = 30
    processes: int = 2


class _Closable(Protocol):
    def close(self) -> None: ...


def _suspended_flags() -> int:
    """CREATE_SUSPENDED on Windows, so the child waits in the job; 0 elsewhere."""
    flags = 0
    if sys.platform == "win32":
        # Imported only on Windows: the module calls ctypes.WinDLL, which does not exist elsewhere.
        from asfb.sandbox import windows

        flags = windows.CREATE_SUSPENDED
    return flags


def _attach(pid: int, active: Limits) -> _Closable | None:
    """Put the child in a new Job Object. None if it could not be created or assigned."""
    job: _Closable | None = None
    if sys.platform == "win32":
        from asfb.sandbox import windows

        job = windows.attach(
            pid,
            memory_bytes=active.memory_bytes,
            cpu_seconds=active.cpu_seconds,
            processes=active.processes,
        )
    return job


@dataclass(frozen=True, slots=True)
class ChildResult:
    """What a child wrote, how it ended. Output is empty after a timeout."""

    returncode: int | None
    output: bytes
    timed_out: bool
    overflow: bool


def _env() -> dict[str, str]:
    return {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}


def _feed(stream: IO[bytes], body: bytes) -> None:
    with suppress(OSError):
        stream.write(body)
    with suppress(OSError):
        stream.close()


def _drain(stream: IO[bytes], limit: int, box: list[bytes]) -> None:
    try:
        box.append(stream.read(limit + 1))
    except OSError:
        box.append(b"")


def _kill(proc: subprocess.Popen[bytes]) -> None:
    with suppress(OSError):
        proc.kill()
    with suppress(OSError, subprocess.TimeoutExpired):
        proc.wait(timeout=_REAP_SECONDS)


def _converse(
    proc: subprocess.Popen[bytes],
    stdin: IO[bytes],
    stdout: IO[bytes],
    body: bytes,
    timeout: float,
    output_limit: int,
) -> ChildResult:
    box: list[bytes] = []
    writer = threading.Thread(target=_feed, args=(stdin, body), daemon=True)
    reader = threading.Thread(target=_drain, args=(stdout, output_limit, box), daemon=True)
    writer.start()
    reader.start()
    deadline = time.monotonic() + timeout
    writer.join(max(0.0, deadline - time.monotonic()))
    reader.join(max(0.0, deadline - time.monotonic()))
    timed_out = writer.is_alive() or reader.is_alive()
    output = box[0] if box else b""
    overflow = len(output) > output_limit
    if not (timed_out or overflow):
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
    if timed_out or overflow:
        _kill(proc)
        writer.join(_JOIN_SECONDS)
        reader.join(_JOIN_SECONDS)
    return ChildResult(
        returncode=proc.returncode,
        output=b"" if timed_out else output,
        timed_out=timed_out,
        overflow=overflow,
    )


def run_child(
    argv: Sequence[str],
    body: bytes,
    *,
    timeout: float,
    limits: Limits | None = None,
    output_limit: int = protocol.HEADER + protocol.MAX_RESPONSE,
) -> ChildResult | ErrorCode:
    """Run argv with body on stdin under the limits and return what it wrote.

    The child is killed and reaped on every path. Test seam: the selftest module uses this too.
    """
    active = Limits() if limits is None else limits
    with tempfile.TemporaryDirectory(prefix="asfb-") as scratch:
        try:
            proc = subprocess.Popen(  # noqa: S603
                list(argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=scratch,
                env=_env(),
                creationflags=_suspended_flags(),
            )
        except OSError:
            return ErrorCode.E_WORKER_START
        job: _Closable | None = None
        try:
            stdin, stdout = proc.stdin, proc.stdout
            if stdin is None or stdout is None:
                return ErrorCode.E_WORKER_START
            if sys.platform == "win32":
                job = _attach(proc.pid, active)
                if job is None:
                    return ErrorCode.E_WORKER_START
            return _converse(proc, stdin, stdout, body, timeout, output_limit)
        finally:
            if proc.poll() is None:
                _kill(proc)
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    with suppress(OSError):
                        stream.close()
            if job is not None:
                job.close()


def _rewrite(data: object, real: Mapping[str, Token]) -> bool:
    """Replace every token string of the dumped document by its real token, in place.

    False if a token string has no pair. Iterative, so a deep document cannot reach the
    recursion limit. Keys are model field names and are not touched.
    """
    pending: list[Any] = [data]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            slots: list[tuple[Any, Any]] = list(node.items())
        elif isinstance(node, list):
            slots = list(enumerate(node))
        else:
            continue
        for index, value in slots:
            if isinstance(value, str):
                if SHAPE_RE.fullmatch(value) is None:
                    continue
                token = real.get(value)
                if token is None:
                    return False
                node[index] = token
            else:
                pending.append(value)
    return True


def _verified(
    job_key: SessionKey, session_key: SessionKey, reply: protocol.OkReply
) -> tuple[protocol.WorkerModel, SurfacePairs] | ErrorCode:
    """Re-key a worker reply from the one-time job key to the session key, then revalidate it.

    Every pair must be the token the job key gives its surface. The real token is the session
    key's token for the same surface. Every token of the document must have a pair, else
    E_WORKER_PAIRS. The worker never sees the session key.
    """
    real: dict[str, Token] = {}
    pairs: list[tuple[Token, str]] = []
    try:
        for job_token, surface in reply.pairs:
            if token_for(job_key, Untrusted(surface)) != job_token:
                return ErrorCode.E_WORKER_PAIRS
            token = token_for(session_key, Untrusted(surface))
            if real.setdefault(job_token, token) != token:
                return ErrorCode.E_WORKER_PAIRS
            if (token, surface) not in pairs:
                pairs.append((token, surface))
    except AsfbError:
        return ErrorCode.E_WORKER_PAIRS
    data = reply.doc.model_dump(mode="json")
    if not _rewrite(data, real):
        return ErrorCode.E_WORKER_PAIRS
    try:
        doc = type(reply.doc).model_validate_json(
            json.dumps(data, separators=(",", ":"), ensure_ascii=False), strict=True
        )
    except ValueError:
        return ErrorCode.E_WORKER_PAIRS
    return doc, SurfacePairs(tuple(pairs))


def _reply(child: ChildResult, job_key: SessionKey, key: SessionKey) -> Outcome | ErrorCode:
    if child.timed_out:
        return ErrorCode.E_WORKER_TIMEOUT
    if child.overflow:
        return ErrorCode.E_WORKER_PROTOCOL
    if child.returncode != 0:
        return ErrorCode.E_WORKER_LIMIT
    body = protocol.unframe(child.output, protocol.MAX_RESPONSE)
    if isinstance(body, ErrorCode):
        return body
    reply = protocol.parse_reply(body)
    if isinstance(reply, ErrorCode):
        return reply
    if isinstance(reply, protocol.ErrReply):
        return ErrorCode(reply.code)
    verified = _verified(job_key, key, reply)
    if isinstance(verified, ErrorCode):
        return verified
    model, pairs = verified
    return model, pairs, tuple(reply.spans)


def _spans_ok(kind: str, model: protocol.WorkerModel, spans: tuple[tuple[int, int], ...]) -> bool:
    """Only a code reply carries spans, and it carries exactly one per lexeme."""
    if kind != "code":
        return spans == ()
    return isinstance(model, CodeV1) and len(spans) == len(model.lexemes)


def wall_seconds(kind: str) -> float:
    """The wall-clock limit of one job of this kind: KIND_WALL_SECONDS, else WALL_SECONDS."""
    return KIND_WALL_SECONDS.get(kind, WALL_SECONDS)


def _job(
    kind: str,
    key: SessionKey,
    profile: Profile,
    text: str,
    *,
    name: str | None = None,
    timeout: float,
    argv: Sequence[str],
    limits: Limits | None,
) -> Outcome | ErrorCode:
    if kind not in protocol.KINDS:
        return ErrorCode.E_WORKER_PROTOCOL
    # The worker gets a one-time key, never the session key. Its tokens are re-keyed in _verified.
    job_key = SessionKey.generate()
    request = protocol.encode_request(kind, job_key.export(), profile.value, text, name)
    if isinstance(request, ErrorCode):
        return request
    child = run_child(argv, request, timeout=timeout, limits=limits)
    if isinstance(child, ErrorCode):
        return child
    outcome = _reply(child, job_key, key)
    if isinstance(outcome, ErrorCode):
        return outcome
    model, _pairs, spans = outcome
    expected = _EXPECTED.get(kind)
    if expected is None or not isinstance(model, expected):
        return ErrorCode.E_WORKER_PROTOCOL
    if not _spans_ok(kind, model, spans):
        return ErrorCode.E_WORKER_PROTOCOL
    return outcome


def run_job_spans(
    kind: str,
    key: SessionKey,
    profile: Profile,
    text: str,
    *,
    name: str | None = None,
    timeout: float | None = None,
    argv: Sequence[str] = WORKER_ARGV,
    limits: Limits | None = None,
) -> Outcome:
    """Run one job in a fresh worker. Returns the model, the verified pairs and the lexeme spans
    (code only, empty for other kinds). Raises AsfbError with a static code. The wall-clock limit
    is KIND_WALL_SECONDS for the kind unless timeout is given."""
    wall = wall_seconds(kind) if timeout is None else timeout
    outcome = _job(kind, key, profile, text, name=name, timeout=wall, argv=argv, limits=limits)
    if isinstance(outcome, ErrorCode):
        raise AsfbError(outcome)
    return outcome


def run_job(
    kind: str,
    key: SessionKey,
    profile: Profile,
    text: str,
    *,
    name: str | None = None,
    timeout: float | None = None,
    argv: Sequence[str] = WORKER_ARGV,
    limits: Limits | None = None,
) -> tuple[protocol.WorkerModel, SurfacePairs]:
    """Run one job in a fresh worker. Returns the model and the verified pairs. Raises AsfbError."""
    model, pairs, _spans = run_job_spans(
        kind, key, profile, text, name=name, timeout=timeout, argv=argv, limits=limits
    )
    return model, pairs
