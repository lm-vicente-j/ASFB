"""The worker's limits and audit hook, exercised through the selftest module.

Hook checks run on every OS. Memory and CPU limits come from the Job Object on Windows only; the
POSIX rlimits are tested with fakes in test_sandbox_posix.py. Each child is reaped by run_child.
"""

import sys
import time
from pathlib import Path

import pytest

from asfb.errors import ErrorCode
from asfb.sandbox import client
from asfb.sandbox.client import ChildResult
from asfb.sandbox.selftest import EXIT_MEMORY, EXIT_REFUSED, run_action
from asfb.types.key import SessionKey

KEY = SessionKey(bytes(range(32)))
JOB = SessionKey(bytes(range(1, 33)))  # stands in for the one-time key of a job
WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Job Object limits: Windows")


def _ran(outcome: ChildResult | ErrorCode) -> ChildResult:
    assert isinstance(outcome, ChildResult)
    return outcome


def test_selftest_runs_an_allowed_action_to_completion() -> None:
    outcome = _ran(run_action("emit", payload=b"ok"))
    assert outcome.returncode == 0
    assert outcome.output == b"ok"
    assert outcome.timed_out is False


def test_socket_is_blocked_by_the_audit_hook() -> None:
    outcome = _ran(run_action("socket"))
    assert outcome.returncode == EXIT_REFUSED
    assert outcome.output == b""


def test_file_write_is_blocked_by_the_audit_hook(tmp_path: Path) -> None:
    target = tmp_path / "probe.txt"
    outcome = _ran(run_action("write", target=str(target)))
    assert outcome.returncode == EXIT_REFUSED
    assert not target.exists()


def test_process_spawn_is_blocked_by_the_audit_hook() -> None:
    outcome = _ran(run_action("spawn"))
    assert outcome.returncode == EXIT_REFUSED


@WINDOWS_ONLY
def test_memory_limit_kills_the_allocation_as_a_worker_limit() -> None:
    outcome = _ran(run_action("alloc", memory_mib=128, cpu_seconds=30, timeout=20.0))
    assert outcome.returncode == EXIT_MEMORY
    assert outcome.output == b""
    assert client._reply(outcome, JOB, KEY) is ErrorCode.E_WORKER_LIMIT  # noqa: SLF001


@WINDOWS_ONLY
def test_cpu_limit_stops_a_spin_as_a_worker_limit() -> None:
    started = time.monotonic()
    outcome = _ran(run_action("spin", cpu_seconds=1, timeout=30.0))
    assert time.monotonic() - started < 10.0
    assert outcome.timed_out is False
    assert outcome.returncode != 0
    assert client._reply(outcome, JOB, KEY) is ErrorCode.E_WORKER_LIMIT  # noqa: SLF001
