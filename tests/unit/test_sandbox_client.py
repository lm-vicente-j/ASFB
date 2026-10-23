"""Main-side client: reply handling, pair checks, and child failures. Child processes are small.

Most protocol cases call the pure reply handler with framed bytes, so they start nothing. The
few tests that do spawn run `python -I -c ...` (no asfb import, about 0.2 s) or the selftest crash
and hang actions, and every child is killed and reaped by run_child before the test returns.
"""

import sys
from pathlib import Path
from typing import Any

import pytest

from asfb import ingest
from asfb.errors import AsfbError, ErrorCode
from asfb.sandbox import client, protocol
from asfb.sandbox.client import ChildResult, Limits
from asfb.sandbox.selftest import run_action
from asfb.sanitize.html import censor_document, censor_with_pairs
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import DocV1, Profile, SourceKind
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted

KEY = SessionKey(bytes(range(32)))
JOB = SessionKey(bytes(range(1, 33)))  # stands in for the one-time key of a job
CANARY = "CANARY-CLI-9053"
OTHER_TOKEN = Token("t_0123456789abcdef")
EMPTY = DocV1(profile=Profile.STRICT, source=SourceKind.HTML, vocab=(), body=())
PY = sys.executable


def _framed_ok(pairs: list[tuple[str, str]]) -> bytes:
    body = protocol.encode_ok(EMPTY, pairs)
    assert isinstance(body, bytes)
    return protocol.frame(body)


def _child(output: bytes, *, returncode: int = 0, timed_out: bool = False) -> ChildResult:
    return ChildResult(
        returncode=returncode, output=output, timed_out=timed_out, overflow=len(output) > 1 << 26
    )


def _child_code(argv: list[str], *, timeout: float = 30.0) -> ErrorCode:
    with pytest.raises(AsfbError) as raised:
        client.run_job(
            "html",
            KEY,
            Profile.STRICT,
            "<p>x</p>",
            argv=argv,
            timeout=timeout,
        )
    return raised.value.code


def test_valid_ok_reply_gives_the_document_and_verified_pairs() -> None:
    job_token = token_for(JOB, Untrusted("x"))
    token = token_for(KEY, Untrusted("x"))
    outcome = client._reply(_child(_framed_ok([(str(job_token), "x")])), JOB, KEY)  # noqa: SLF001
    assert not isinstance(outcome, ErrorCode)
    doc, verified, _spans = outcome
    assert doc == EMPTY
    assert verified.items() == ((token, "x"),)


def test_pair_under_another_key_is_a_pairs_mismatch() -> None:
    outcome = client._reply(  # noqa: SLF001
        _child(_framed_ok([(str(OTHER_TOKEN), CANARY)])), JOB, KEY
    )
    assert outcome is ErrorCode.E_WORKER_PAIRS
    assert CANARY not in str(outcome)


def test_pair_minted_under_the_session_key_is_a_pairs_mismatch() -> None:
    # The worker never holds the session key, so a pair made with it cannot be genuine.
    session_token = token_for(KEY, Untrusted("x"))
    outcome = client._reply(_child(_framed_ok([(str(session_token), "x")])), JOB, KEY)  # noqa: SLF001
    assert outcome is ErrorCode.E_WORKER_PAIRS


def test_pair_check_is_an_hmac_check_not_a_format_check() -> None:
    real = censor_document(JOB, ingest.read_text("<p>ab</p>"), Profile.STRICT)
    body = protocol.encode_ok(real, [(str(OTHER_TOKEN), "ab")])
    assert isinstance(body, bytes)
    outcome = client._reply(_child(protocol.frame(body)), JOB, KEY)  # noqa: SLF001
    assert outcome is ErrorCode.E_WORKER_PAIRS


def test_document_token_without_a_pair_is_a_pairs_mismatch() -> None:
    real = censor_document(JOB, ingest.read_text("<p>ab</p>"), Profile.STRICT)
    body = protocol.encode_ok(real, [])
    assert isinstance(body, bytes)
    outcome = client._reply(_child(protocol.frame(body)), JOB, KEY)  # noqa: SLF001
    assert outcome is ErrorCode.E_WORKER_PAIRS


def test_reply_is_rekeyed_to_the_session_key() -> None:
    nodes = ingest.read_text("<p>ab</p>")
    job_doc, job_pairs = censor_with_pairs(JOB, nodes, Profile.STRICT)
    session_doc, session_pairs = censor_with_pairs(KEY, nodes, Profile.STRICT)
    body = protocol.encode_ok(job_doc, [(str(t), s) for t, s in job_pairs.items()])
    assert isinstance(body, bytes)
    outcome = client._reply(_child(protocol.frame(body)), JOB, KEY)  # noqa: SLF001
    assert not isinstance(outcome, ErrorCode)
    doc, verified, _spans = outcome
    assert doc == session_doc
    assert verified.items() == session_pairs.items()


def test_lone_surrogate_in_a_reply_is_a_protocol_error() -> None:
    # A JSON escape for a lone surrogate parses to a str the reply model refuses (not encodable).
    doc = EMPTY.model_dump_json()
    body = f'{{"v":1,"ok":true,"doc":{doc},"pairs":[["{OTHER_TOKEN}","\\ud800"]]}}'
    outcome = client._reply(_child(protocol.frame(body.encode("ascii"))), JOB, KEY)  # noqa: SLF001
    assert outcome is ErrorCode.E_WORKER_PROTOCOL


def test_error_reply_gives_its_worker_code() -> None:
    framed = protocol.frame(protocol.encode_error(ErrorCode.E_HTML_NODES))
    assert client._reply(_child(framed), JOB, KEY) is ErrorCode.E_HTML_NODES  # noqa: SLF001


def test_timeout_wins_over_any_output() -> None:
    result = _child(b"", timed_out=True, returncode=-9)
    assert client._reply(result, JOB, KEY) is ErrorCode.E_WORKER_TIMEOUT  # noqa: SLF001


def test_nonzero_exit_is_a_limit_even_with_output() -> None:
    result = _child(_framed_ok([]), returncode=3)
    assert client._reply(result, JOB, KEY) is ErrorCode.E_WORKER_LIMIT  # noqa: SLF001


def test_no_output_with_zero_exit_is_a_protocol_error() -> None:
    assert client._reply(_child(b""), JOB, KEY) is ErrorCode.E_WORKER_PROTOCOL  # noqa: SLF001


def test_oversized_output_is_a_protocol_error() -> None:
    result = ChildResult(returncode=0, output=b"x", timed_out=False, overflow=True)
    assert client._reply(result, JOB, KEY) is ErrorCode.E_WORKER_PROTOCOL  # noqa: SLF001


@pytest.mark.parametrize(
    "output",
    [
        b"\x00\x00\x00\x09{}",  # length says 9, body has 2
        b"\x00\x00\x00\x02{}\x00",  # trailing byte
        b'\x00\x00\x00\x03{"}',  # invalid JSON
        b"\x00\x00",  # short header
        b"\x00\x00\x00\x02\xff\xfe",  # not UTF-8
    ],
    ids=["bad-length", "trailing", "invalid-json", "short-header", "not-utf8"],
)
def test_malformed_framing_is_a_protocol_error(output: bytes) -> None:
    assert client._reply(_child(output), JOB, KEY) is ErrorCode.E_WORKER_PROTOCOL  # noqa: SLF001


def test_ok_reply_with_an_extra_key_is_a_protocol_error() -> None:
    body = b'{"v":1,"ok":true,"doc":' + EMPTY.model_dump_json().encode() + b',"pairs":[],"extra":1}'
    assert client._reply(_child(protocol.frame(body)), JOB, KEY) is ErrorCode.E_WORKER_PROTOCOL  # noqa: SLF001


def test_error_reply_with_a_code_outside_the_subset_is_a_protocol_error() -> None:
    body = b'{"v":1,"ok":false,"code":"E_EGRESS_TYPE"}'
    assert client._reply(_child(protocol.frame(body)), JOB, KEY) is ErrorCode.E_WORKER_PROTOCOL  # noqa: SLF001


def test_request_for_an_unknown_kind_is_refused_before_spawning() -> None:
    assert (
        client._job(  # noqa: SLF001
            "junit",
            KEY,
            Profile.STRICT,
            "<p>x</p>",
            timeout=5.0,
            argv=[PY, "-I", "-c", "pass"],
            limits=None,
        )
        is ErrorCode.E_WORKER_PROTOCOL
    )


def test_missing_executable_is_a_start_error() -> None:
    argv = [str(Path(PY).with_name("no-such-interpreter-asfb.exe"))]
    assert _child_code(argv) is ErrorCode.E_WORKER_START


def test_oversized_text_is_refused_before_spawning() -> None:
    text = "a" * (protocol.MAX_REQUEST + 1)
    with pytest.raises(AsfbError) as raised:
        client.run_job(
            "html", KEY, Profile.STRICT, text, argv=[PY, "-I", "-c", "pass"], timeout=5.0
        )
    assert raised.value.code is ErrorCode.E_SOURCE_SIZE


def test_non_str_text_is_refused_before_spawning() -> None:
    raw: Any = b"<p>x</p>"
    with pytest.raises(AsfbError) as raised:
        client.run_job(
            "html",
            KEY,
            Profile.STRICT,
            raw,
            argv=[PY, "-I", "-c", "pass"],
            timeout=5.0,
        )
    assert raised.value.code is ErrorCode.E_SOURCE_UNSUPPORTED


def test_hang_is_killed_at_the_wall_clock_timeout() -> None:
    argv = [PY, "-I", "-c", "import time; time.sleep(60)"]
    assert _child_code(argv, timeout=0.5) is ErrorCode.E_WORKER_TIMEOUT


def test_each_kind_has_its_wall_clock_limit() -> None:
    assert client.KIND_WALL_SECONDS == {"code": 15.0}
    assert client.wall_seconds("code") == 15.0
    assert client.wall_seconds("html") == client.WALL_SECONDS == 60.0


def test_a_code_job_past_its_fifteen_seconds_is_killed_and_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real limit is 15 s; the test shortens it so that the hang ends quickly.
    monkeypatch.setattr(client, "KIND_WALL_SECONDS", {"code": 0.5})
    argv = [PY, "-I", "-c", "import time; time.sleep(60)"]
    with pytest.raises(AsfbError) as raised:
        client.run_job_spans("code", KEY, Profile.STRICT, "x = 1\n", argv=argv)
    assert raised.value.code is ErrorCode.E_WORKER_TIMEOUT


def test_hang_is_reaped_by_run_child() -> None:
    outcome = client.run_child([PY, "-I", "-c", "import time; time.sleep(60)"], b"", timeout=0.5)
    assert isinstance(outcome, ChildResult)
    assert outcome.timed_out is True
    assert outcome.returncode is not None


def test_crash_without_a_reply_is_a_limit() -> None:
    assert _child_code([PY, "-I", "-c", "import os; os._exit(7)"]) is ErrorCode.E_WORKER_LIMIT


def test_stray_stdout_before_the_frame_is_a_protocol_error() -> None:
    frame = protocol.frame(protocol.encode_error(ErrorCode.E_HTML_PARSE))
    script = (
        "import sys; sys.stdout.buffer.write(b'junk'); "
        f"sys.stdout.buffer.write({frame!r}); sys.stdout.buffer.flush()"
    )
    assert _child_code([PY, "-I", "-c", script]) is ErrorCode.E_WORKER_PROTOCOL


def test_stderr_of_the_child_never_reaches_the_parent(capfd: pytest.CaptureFixture[str]) -> None:
    frame = protocol.frame(protocol.encode_error(ErrorCode.E_HTML_PARSE))
    script = (
        f"import sys; sys.stderr.write({CANARY!r}); sys.stderr.flush(); "
        f"sys.stdout.buffer.write({frame!r}); sys.stdout.buffer.flush()"
    )
    outcome = client.run_child([PY, "-I", "-c", script], b"", timeout=10.0)
    assert isinstance(outcome, ChildResult)
    assert CANARY.encode() not in outcome.output
    captured = capfd.readouterr()
    assert CANARY not in captured.out
    assert CANARY not in captured.err
    assert _child_code([PY, "-I", "-c", script]) is ErrorCode.E_HTML_PARSE


def test_selftest_crash_action_is_a_limit() -> None:
    outcome = run_action("crash", timeout=20.0)
    assert isinstance(outcome, ChildResult)
    assert client._reply(outcome, JOB, KEY) is ErrorCode.E_WORKER_LIMIT  # noqa: SLF001


def test_limits_default_to_the_specified_values() -> None:
    limits = Limits()
    assert limits.memory_bytes == 512 * 1024 * 1024
    assert limits.cpu_seconds == 30
    assert limits.processes == 2
    assert client.WALL_SECONDS == 60.0
    assert client.WORKER_ARGV[1:] == ("-I", "-m", "asfb.sandbox.worker")


def test_worker_environment_keeps_only_the_four_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASFB_SECRET_ENV", CANARY)
    env = client._env()  # noqa: SLF001
    assert set(env) <= {"SYSTEMROOT", "PATH", "TEMP", "TMP"}
    assert CANARY not in env.values()


def _captured_requests(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    sent: list[bytes] = []
    real_run_child = client.run_child

    def capture(argv: Any, body: bytes, **kwargs: Any) -> ChildResult | ErrorCode:
        sent.append(body)
        return real_run_child(argv, body, **kwargs)

    monkeypatch.setattr(client, "run_child", capture)
    return sent


def test_session_key_never_reaches_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SessionKey(bytes(range(32)))
    sent = _captured_requests(monkeypatch)
    client.run_job("html", session, Profile.STRICT, "<p>x</p>")
    assert len(sent) == 1
    assert session.export() not in sent[0]
    assert session.export().hex().encode("ascii") not in sent[0]


def test_each_job_gets_its_own_one_time_key(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SessionKey(bytes(range(32)))
    sent = _captured_requests(monkeypatch)
    client.run_job("html", session, Profile.STRICT, "<p>x</p>")
    client.run_job("html", session, Profile.STRICT, "<p>x</p>")
    keys = []
    for body in sent:
        payload = protocol.unframe(body, protocol.MAX_REQUEST)
        assert isinstance(payload, bytes)
        request = protocol.parse_request(payload)
        assert isinstance(request, protocol.Request)
        keys.append(request.key)
    assert len(keys) == 2
    assert keys[0] != keys[1]
    assert session.export().hex() not in keys


def test_run_job_matches_the_in_process_censor() -> None:
    source = "<p class='a b'>Alice</p><a href='https://example.com/x?q=1'>link</a>"
    doc, pairs = client.run_job("html", KEY, Profile.STRICT, source)
    expected_doc, expected_pairs = censor_with_pairs(KEY, ingest.read_text(source), Profile.STRICT)
    assert doc == expected_doc
    assert set(pairs.items()) == set(expected_pairs.items())
