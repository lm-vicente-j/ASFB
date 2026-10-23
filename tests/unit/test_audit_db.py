"""The SQLite job audit rules, tested without installing the hook (a hook cannot be removed)."""

import pytest

from asfb.sandbox import audit


@pytest.mark.parametrize(
    ("event", "args"),
    [
        ("sqlite3.connect", (":memory:",)),
        ("sqlite3.connect/handle", ("handle",)),
        ("sqlite3.enable_load_extension", ("connection", False)),
        ("open", ("notes.txt", "r", 0)),
        ("import", ("sqlite3", None, None)),
    ],
)
def test_ordinary_events_pass_during_a_db_job(event: str, args: tuple[object, ...]) -> None:
    with audit.db_job():
        assert audit.blocked(event, args) is False


@pytest.mark.parametrize("args", [("app.db",), ("/data/app.db",), ("file:x?mode=ro",), ("",), ()])
def test_a_file_connection_is_refused_during_a_db_job(args: tuple[object, ...]) -> None:
    with audit.db_job():
        assert audit.blocked("sqlite3.connect", args) is True


def test_extensions_are_refused_during_a_db_job() -> None:
    with audit.db_job():
        assert audit.blocked("sqlite3.enable_load_extension", ("connection", True)) is True
        assert audit.blocked("sqlite3.load_extension", ("connection", "ext")) is True


def test_a_malformed_extension_toggle_is_refused() -> None:
    with audit.db_job():
        assert audit.blocked("sqlite3.enable_load_extension", ("connection",)) is True
        assert audit.blocked("sqlite3.enable_load_extension", ("connection", 0)) is True


def test_the_rules_end_with_the_job() -> None:
    with audit.db_job():
        pass
    assert audit.blocked("sqlite3.connect", ("app.db",)) is False
    assert audit.blocked("sqlite3.load_extension", ("connection", "ext")) is False


def test_the_rules_end_even_when_the_job_fails() -> None:
    with pytest.raises(RuntimeError), audit.db_job():
        raise RuntimeError("inside the job")
    assert audit.blocked("sqlite3.connect", ("app.db",)) is False


def test_code_and_db_jobs_do_not_leak_into_each_other() -> None:
    with audit.code_job():
        assert audit.blocked("sqlite3.connect", ("app.db",)) is False
    with audit.db_job():
        assert audit.blocked("compile", ("src", "f", "exec")) is False
