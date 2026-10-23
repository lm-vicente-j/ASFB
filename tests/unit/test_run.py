"""Session.run with real pytest on a temporary project, plus the refusals that need no child."""

import sys
from pathlib import Path

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.runner import netns
from asfb.runner.execute import Finished
from asfb.session import Session
from tests.junit_samples import KEY

PY = sys.executable
PASSING_AND_FAILING = """
def test_passes():
    assert True


def test_fails():
    assert 1 == 2, "message"
"""


def _project(root: Path, body: str) -> Path:
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text(body, encoding="utf-8")
    return root


def _session(root: Path, **options: object) -> Session:
    values: dict[str, object] = {
        "key": KEY,
        "python": PY,
        "run_network": "host",
        "run_timeout": 120,
    }
    values.update(options)
    return Session([str(root)], **values)  # type: ignore[arg-type]


def _code(action: object) -> ErrorCode:
    assert callable(action)
    with pytest.raises(AsfbError) as raised:
        action()
    return raised.value.code


def test_real_pytest_run_reports_failures_as_public_and_censored_names(tmp_path: Path) -> None:
    root = _project(tmp_path / "proj", PASSING_AND_FAILING)
    result = _session(root).run("pytest", ["tests"])
    assert (
        result.view.splitlines()[0]
        == "pytest exit=tests_failed report=parsed tests=2 failures=1 errors=0 skipped=0"
    )
    # pytest 9 writes no type attribute, so the exception name sits inside the censored message.
    assert result.view.splitlines()[2].startswith("FAILED ")
    assert "test_fails" not in result.view
    assert '"schema_id":"asfb.run/1"' in result.json


def test_in_process_parser_gives_the_same_summary(tmp_path: Path) -> None:
    root = _project(tmp_path / "proj", PASSING_AND_FAILING)
    isolated = _session(root).run("pytest", ["tests"])
    in_process = _session(root, isolate=False).run("pytest", ["tests"])
    assert isolated.json == in_process.json


def test_unknown_tool_is_refused(tmp_path: Path) -> None:
    session = _session(_project(tmp_path / "proj", PASSING_AND_FAILING))
    assert _code(lambda: session.run("mypy", ["."])) is ErrorCode.E_RUN_TOOL


def test_targets_outside_the_root_are_refused(tmp_path: Path) -> None:
    session = _session(_project(tmp_path / "proj", PASSING_AND_FAILING))
    assert _code(lambda: session.run("pytest", ["/etc"])) is ErrorCode.E_PATH_OUTSIDE_ROOT


@pytest.mark.parametrize("targets", [[], ["."] * 33, [5]])
def test_bad_target_lists_are_refused(tmp_path: Path, targets: list[object]) -> None:
    session = _session(_project(tmp_path / "proj", PASSING_AND_FAILING))
    assert _code(lambda: session.run("pytest", targets)) is ErrorCode.E_RUN_TARGETS  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [0, 3601, True])
def test_run_timeout_outside_one_to_3600_is_refused(tmp_path: Path, timeout: object) -> None:
    root = _project(tmp_path / "proj", PASSING_AND_FAILING)
    assert _code(lambda: _session(root, run_timeout=timeout)) is ErrorCode.E_RUN_CONFIG


def test_unknown_run_network_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path / "proj", PASSING_AND_FAILING)
    assert _code(lambda: _session(root, run_network="net")) is ErrorCode.E_RUN_CONFIG


def test_timeout_kills_the_run_and_drops_the_report(tmp_path: Path) -> None:
    body = "import time\n\n\ndef test_sleeps():\n    time.sleep(30)\n"
    root = _project(tmp_path / "proj", body)
    result = _session(root, run_timeout=1).run("pytest", ["tests"])
    assert result.view == "pytest exit=timeout report=dropped"


def test_missing_report_is_dropped_not_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asfb.session as session_module

    monkeypatch.setattr(session_module, "execute", lambda *a, **k: Finished(0, False))
    root = _project(tmp_path / "proj", PASSING_AND_FAILING)
    result = _session(root).run("pytest", ["tests"])
    assert result.view == "pytest exit=ok report=dropped"


def test_missing_prefix_refuses_isolated_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(netns, "isolate_prefix", lambda: None)
    root = _project(tmp_path / "proj", PASSING_AND_FAILING)
    session = _session(root, run_network="isolate")
    assert _code(lambda: session.run("pytest", ["tests"])) is ErrorCode.E_RUN_NETWORK


def test_a_missing_interpreter_is_a_start_error(tmp_path: Path) -> None:
    root = _project(tmp_path / "proj", PASSING_AND_FAILING)
    session = _session(root, python=str(tmp_path / "no-such-python"))
    assert _code(lambda: session.run("pytest", ["tests"])) is ErrorCode.E_RUN_START
