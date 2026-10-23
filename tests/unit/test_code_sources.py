"""Python as a source: the API, the session, the worker protocol, the code job and the audit gate.

Isolated and in-process results must be equal, a latin-1 cookie is refused with its code in both,
and nothing outside the lexemes (names, strings, comments, file names) reaches the output.
"""

import io
import json
from pathlib import Path

import pytest

from asfb import api
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import code
from asfb.sandbox import audit, client, protocol, worker
from asfb.sanitize.code import censor_code
from asfb.schema.code import CodeV1
from asfb.schema.doc import Profile
from asfb.session import Listing, Sanitized, Session
from asfb.types.key import SessionKey
from asfb.types.token import Token

KEY = SessionKey(bytes(range(32)))
SOURCE = (
    "import os\n\n\ndef load(path, user):\n"
    "    # reads the config\n"
    "    msg = f'hello {user.name}'\n"
    "    return os.path.join(path, msg)  # done\n"
)
LATIN1 = "# -*- coding: latin-1 -*-\nx = 1\n"


def _refused(call: object) -> ErrorCode:
    with pytest.raises(AsfbError) as info:
        call()  # type: ignore[operator]
    return info.value.code


def test_isolated_python_equals_in_process() -> None:
    in_process = api.sanitize_python(SOURCE, key=KEY)
    isolated = api.sanitize_python(SOURCE, key=KEY, isolate=True)
    assert isolated.json == in_process.json
    assert in_process.json.startswith('{"schema_id":"asfb.code/1"')


@pytest.mark.parametrize("suffix", [".py", ".pyi", ".PY"])
def test_python_suffixes_are_sanitized_as_code(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"tool{suffix}"
    path.write_text(SOURCE, encoding="utf-8")
    from_file = json.loads(api.sanitize(str(path), key=KEY).json)
    from_text = json.loads(api.sanitize_python(SOURCE, key=KEY).json)
    assert from_file["lexemes"] == from_text["lexemes"]
    assert from_file["ext"] == suffix.lower().lstrip(".")
    assert from_text["file"] is None


def test_api_refuses_a_latin1_cookie_with_its_code(tmp_path: Path) -> None:
    path = tmp_path / "old.py"
    path.write_bytes(LATIN1.encode("ascii"))
    assert (
        _refused(lambda: api.sanitize(str(path), key=KEY)) is ErrorCode.E_CODE_ENCODING_UNSUPPORTED
    )
    assert (
        _refused(lambda: api.sanitize(str(path), key=KEY, isolate=True))
        is ErrorCode.E_CODE_ENCODING_UNSUPPORTED
    )


def test_api_refuses_latin1_bytes_with_the_encoding_code(tmp_path: Path) -> None:
    path = tmp_path / "bytes.py"
    path.write_bytes("x = 'é'\n".encode("latin-1"))
    assert (
        _refused(lambda: api.sanitize(str(path), key=KEY)) is ErrorCode.E_CODE_ENCODING_UNSUPPORTED
    )


def test_isolated_latin1_cookie_is_the_static_code_not_an_internal_error() -> None:
    assert (
        _refused(lambda: api.sanitize_python(LATIN1, key=KEY, isolate=True))
        is ErrorCode.E_CODE_ENCODING_UNSUPPORTED
    )


def test_session_sanitizes_a_python_file_with_a_token_name(tmp_path: Path) -> None:
    (tmp_path / "loader.py").write_text(SOURCE, encoding="utf-8")
    session = Session([str(tmp_path)], key=KEY, isolate=True)
    result = session.sanitize("loader.py")
    assert isinstance(result, Sanitized)
    view = result.view
    assert view.splitlines()[0].endswith(".py")
    assert "loader" not in view
    assert "reads" not in view
    assert "hello" not in view
    assert "os . path . join" in view


def test_session_isolated_equals_in_process(tmp_path: Path) -> None:
    (tmp_path / "loader.py").write_text(SOURCE, encoding="utf-8")
    isolated = Session([str(tmp_path)], key=KEY, isolate=True).sanitize("loader.py")
    in_process = Session([str(tmp_path)], key=KEY, isolate=False).sanitize("loader.py")
    assert isinstance(isolated, Sanitized)
    assert isinstance(in_process, Sanitized)
    assert isolated.json == in_process.json
    assert isolated.view == in_process.view


def test_session_refuses_a_latin1_cookie_through_the_worker(tmp_path: Path) -> None:
    (tmp_path / "old.py").write_bytes(LATIN1.encode("ascii"))
    session = Session([str(tmp_path)], key=KEY, isolate=True)
    assert _refused(lambda: session.sanitize("old.py")) is ErrorCode.E_CODE_ENCODING_UNSUPPORTED


def test_session_listing_includes_python_entries(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "stub.pyi").write_text("x: int\n", encoding="utf-8")
    result = Session([str(tmp_path)], key=KEY, isolate=False).sanitize(".")
    assert isinstance(result, Listing)
    assert sorted(line.rsplit(".", 1)[1] for line in result.view.splitlines()) == ["py", "pyi"]


def test_a_token_path_resolves_a_python_file(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    session = Session([str(tmp_path)], key=KEY, isolate=False)
    listed = session.sanitize(".")
    assert isinstance(listed, Listing)
    path = listed.view.split()[1]
    assert path.startswith("t_")
    assert path.endswith(".py")
    assert isinstance(session.sanitize(path), Sanitized)


def test_run_job_returns_a_code_model_and_one_span_per_lexeme() -> None:
    model, pairs, spans = client.run_job_spans("code", KEY, Profile.STRICT, SOURCE)
    assert isinstance(model, CodeV1)
    assert len(spans) == len(model.lexemes)
    assert len(pairs.items()) > 0


def test_spans_belong_to_code_replies_and_come_one_per_lexeme() -> None:
    model = censor_code(KEY, code.read_text("x = 1\n"), Profile.STRICT, None).model
    assert client._spans_ok("code", model, ((0, 1),) * len(model.lexemes))  # noqa: SLF001
    assert not client._spans_ok("code", model, ())  # noqa: SLF001
    assert not client._spans_ok("html", model, ((0, 1),) * len(model.lexemes))  # noqa: SLF001
    assert client._spans_ok("html", model, ())  # noqa: SLF001


def _request(**overrides: object) -> bytes:
    body: dict[str, object] = {
        "v": 1,
        "kind": "code",
        "key": KEY.export().hex(),
        "profile": "strict",
        "text": "x = 1\n",
        "name": None,
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


def test_worker_code_job_replies_with_model_pairs_and_spans() -> None:
    reply = _serve(_request(name="tool.py"))
    assert reply["ok"] is True
    assert reply["doc"]["schema_id"] == "asfb.code/1"  # type: ignore[index]
    assert len(reply["spans"]) == len(reply["doc"]["lexemes"])  # type: ignore[arg-type, index]


def test_worker_refuses_a_name_that_is_not_python() -> None:
    reply = _serve(_request(name="tool.txt"))
    assert reply == {"v": 1, "ok": False, "code": "E_SOURCE_UNSUPPORTED"}


def test_worker_refuses_an_overlong_name_as_a_protocol_error() -> None:
    reply = _serve(_request(name="a" * (protocol.MAX_NAME + 1) + ".py"))
    assert reply == {"v": 1, "ok": False, "code": "E_WORKER_PROTOCOL"}


def test_request_name_must_be_a_string_or_null() -> None:
    assert _serve(_request(name=3)) == {"v": 1, "ok": False, "code": "E_WORKER_PROTOCOL"}


def test_code_job_blocks_compile_and_exec_only_while_it_runs() -> None:
    assert audit.blocked("compile", ("src", "f", "exec")) is False
    with audit.code_job():
        assert audit.blocked("compile", ("src", "f", "exec")) is True
        assert audit.blocked("exec", ("code",)) is True
        assert audit.blocked("import", ("json", None, None)) is False
        assert audit.blocked("socket.connect", ()) is True
    assert audit.blocked("compile", ("src", "f", "exec")) is False


def test_code_job_lifts_the_refusal_when_it_raises() -> None:
    with pytest.raises(RuntimeError), audit.code_job():
        raise RuntimeError
    assert audit.blocked("exec", ("code",)) is False


def test_token_type_is_used_for_file_names() -> None:
    model, _pairs, _spans = client.run_job_spans(
        "code", KEY, Profile.STRICT, "x = 1\n", name="payroll.py"
    )
    assert isinstance(model, CodeV1)
    assert isinstance(model.file, Token)
