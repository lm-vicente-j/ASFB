"""Logs as sources: the API, the session, the listing, the edit refusal, the record diff and the
worker protocol. Isolated and in-process results must be equal, and nothing of a log is editable."""

import io
import json
from pathlib import Path

import pytest

from asfb import api
from asfb.errors import AsfbError, ErrorCode
from asfb.sandbox import protocol, worker
from asfb.session import Sanitized, Session
from asfb.types.key import SessionKey

KEY = SessionKey(bytes(range(32)))
TEXT_LOG = "2024-05-01T10:00:00Z INFO app: started by alice\n2024-05-01T10:00:01Z WARN db: slow\n"
JSON_LOG = '{"timestamp":"2024-05-01T10:00:00Z","msg":"a","n":3}\n{"msg": broken\n'


def _session(root: Path, *, isolate: bool = False) -> Session:
    return Session([str(root)], key=KEY, isolate=isolate)


def _sanitized(session: Session, path: str) -> Sanitized:
    """The censored file, for a path that names a file rather than a directory."""
    result = session.sanitize(path)
    assert isinstance(result, Sanitized)
    return result


def _refused(call: object) -> ErrorCode:
    with pytest.raises(AsfbError) as info:
        call()  # type: ignore[operator]
    return info.value.code


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("app.log", TEXT_LOG),
        ("events.jsonl", JSON_LOG),
        ("events.ndjson", JSON_LOG),
        ("A.LOG", TEXT_LOG),
    ],
)
def test_isolated_log_equals_in_process(tmp_path: Path, name: str, text: str) -> None:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    in_process = api.sanitize(str(path), key=KEY)
    isolated = api.sanitize(str(path), key=KEY, isolate=True)
    assert isolated.json == in_process.json
    assert json.loads(in_process.json)["schema_id"] == "asfb.log/1"


def test_format_follows_the_suffix(tmp_path: Path) -> None:
    text_path = tmp_path / "app.log"
    json_path = tmp_path / "events.jsonl"
    text_path.write_text(TEXT_LOG, encoding="utf-8")
    json_path.write_text(JSON_LOG, encoding="utf-8")
    assert json.loads(api.sanitize(str(text_path), key=KEY).json)["format"] == "text"
    assert json.loads(api.sanitize(str(json_path), key=KEY).json)["format"] == "jsonl"


def test_latin1_log_is_refused_with_its_code(tmp_path: Path) -> None:
    path = tmp_path / "old.log"
    path.write_bytes("INFO caf\xe9\n".encode("latin-1"))
    assert (
        _refused(lambda: api.sanitize(str(path), key=KEY)) is ErrorCode.E_LOG_ENCODING_UNSUPPORTED
    )
    assert (
        _refused(lambda: api.sanitize(str(path), key=KEY, isolate=True))
        is ErrorCode.E_LOG_ENCODING_UNSUPPORTED
    )
    assert _refused(lambda: _session(tmp_path).sanitize(str(path))) is (
        ErrorCode.E_LOG_ENCODING_UNSUPPORTED
    )


def test_size_limit_refuses_the_whole_file_with_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "big.log"
    path.write_text(TEXT_LOG, encoding="utf-8")
    monkeypatch.setattr(api, "MAX_LOG_BYTES", 8)
    assert _refused(lambda: api.sanitize(str(path), key=KEY)) is ErrorCode.E_LOG_TOO_LARGE
    monkeypatch.setattr("asfb.session.MAX_LOG_BYTES", 8)
    assert _refused(lambda: _session(tmp_path).sanitize(str(path))) is ErrorCode.E_LOG_TOO_LARGE


def test_listing_names_each_log_with_its_extension(tmp_path: Path) -> None:
    (tmp_path / "app.log").write_text(TEXT_LOG, encoding="utf-8")
    (tmp_path / "events.jsonl").write_text(JSON_LOG, encoding="utf-8")
    (tmp_path / "old.ndjson").write_text(JSON_LOG, encoding="utf-8")
    listing = _session(tmp_path).sanitize(".")
    extensions = sorted(line.rsplit(".", 1)[1] for line in str(listing.view).splitlines())
    assert extensions == ["jsonl", "log", "ndjson"]


def test_edit_of_a_log_is_refused_and_the_file_is_untouched(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text(TEXT_LOG, encoding="utf-8")
    session = _session(tmp_path)
    stored: Sanitized = _sanitized(session, str(path))
    ref = f"{stored.ref.doc}@{stored.ref.revision}"
    assert _refused(lambda: session.edit(ref, "INFO", "WARN")) is (
        ErrorCode.E_EDIT_UNSUPPORTED_KIND
    )
    assert path.read_text(encoding="utf-8") == TEXT_LOG


def test_edit_refusal_comes_before_the_stale_check(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text(TEXT_LOG, encoding="utf-8")
    session = _session(tmp_path)
    stored = _sanitized(session, str(path))
    path.write_text(TEXT_LOG + "INFO later\n", encoding="utf-8")
    ref = f"{stored.ref.doc}@{stored.ref.revision}"
    assert _refused(lambda: session.edit(ref, "INFO", "WARN")) is (
        ErrorCode.E_EDIT_UNSUPPORTED_KIND
    )


def test_diff_of_two_log_revisions_is_by_record(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text(TEXT_LOG, encoding="utf-8")
    session = _session(tmp_path)
    first = _sanitized(session, str(path))
    path.write_text(TEXT_LOG + "ERROR disk full\n", encoding="utf-8")
    second = _sanitized(session, str(path))
    diffed = session.diff(
        f"{first.ref.doc}@{first.ref.revision}", f"{second.ref.doc}@{second.ref.revision}"
    )
    model = json.loads(diffed.json)
    assert model["units"] == "record"
    assert [(hunk["op"], hunk["a_start"], hunk["b_start"]) for hunk in model["hunks"]] == [
        ("insert", 2, 2)
    ]
    assert "+ERROR " in str(diffed.view)


def test_diff_of_a_log_and_a_text_file_is_by_line(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    text_path = tmp_path / "notes.txt"
    log_path.write_text(TEXT_LOG, encoding="utf-8")
    text_path.write_text("alpha\nbeta\n", encoding="utf-8")
    session = _session(tmp_path)
    log_ref = _sanitized(session, str(log_path)).ref
    text_ref = _sanitized(session, str(text_path)).ref
    diffed = session.diff(
        f"{log_ref.doc}@{log_ref.revision}", f"{text_ref.doc}@{text_ref.revision}"
    )
    assert json.loads(diffed.json)["units"] == "line"


def _request(**overrides: object) -> bytes:
    body: dict[str, object] = {
        "v": 1,
        "kind": "log",
        "key": KEY.export().hex(),
        "profile": "strict",
        "text": TEXT_LOG,
        "name": "app.log",
    }
    body.update(overrides)
    return protocol.frame(json.dumps(body).encode("utf-8"))


def _serve(data: bytes) -> dict[str, object]:
    out = io.BytesIO()
    worker.serve(io.BytesIO(data), out)
    body = protocol.unframe(out.getvalue(), protocol.MAX_RESPONSE)
    assert isinstance(body, bytes)
    parsed: dict[str, object] = json.loads(body)
    return parsed


def test_worker_log_job_replies_with_a_log_model_and_no_spans() -> None:
    reply = _serve(_request())
    assert reply["ok"] is True
    assert reply["doc"]["schema_id"] == "asfb.log/1"  # type: ignore[index]
    assert reply["spans"] == []


def test_worker_takes_the_format_from_the_name() -> None:
    reply = _serve(_request(name="events.jsonl", text=JSON_LOG))
    assert reply["doc"]["format"] == "jsonl"  # type: ignore[index]


def test_worker_refuses_a_name_that_is_not_a_log() -> None:
    reply = _serve(_request(name="tool.py"))
    assert reply == {"v": 1, "ok": False, "code": "E_WORKER_PROTOCOL"}


def test_worker_log_job_without_a_name_reads_plain_text() -> None:
    reply = _serve(_request(name=None))
    assert reply["doc"]["format"] == "text"  # type: ignore[index]


def test_worker_log_job_turns_a_refused_source_into_its_code() -> None:
    reply = _serve(_request(text="INFO " + "x" * 70_000 + "\n"))
    assert reply == {"v": 1, "ok": False, "code": "E_LOG_LINE_TOO_LONG"}
