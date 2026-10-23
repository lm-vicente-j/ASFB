"""The junit kind through the worker: in process with respond(), and isolated for real."""

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.sandbox import protocol, worker
from asfb.sandbox.client import run_job
from asfb.schema.doc import Profile
from asfb.schema.run import JunitReport
from tests.junit_samples import KEY, junit_xml


def _reply(request: bytes) -> protocol.OkReply | protocol.ErrReply:
    body = protocol.unframe(worker.respond(request), protocol.MAX_RESPONSE)
    assert not isinstance(body, ErrorCode)
    reply = protocol.parse_reply(body)
    assert not isinstance(reply, ErrorCode)
    return reply


def test_worker_answers_a_junit_request_with_a_junit_report() -> None:
    request = protocol.encode_request("junit", KEY.export(), "strict", junit_xml())
    assert not isinstance(request, ErrorCode)
    reply = _reply(request)
    assert isinstance(reply, protocol.OkReply)
    assert isinstance(reply.doc, JunitReport)
    assert reply.doc.totals.tests == 5


def test_worker_refuses_a_declaration_with_a_run_parse_code() -> None:
    request = protocol.encode_request("junit", KEY.export(), "strict", "<!DOCTYPE x><t/>")
    assert not isinstance(request, ErrorCode)
    reply = _reply(request)
    assert isinstance(reply, protocol.ErrReply)
    assert reply.code == "E_RUN_PARSE"


def test_isolated_junit_job_equals_the_in_process_result() -> None:
    from asfb.ingest.junit import read_junit
    from asfb.sanitize.junit import censor_junit

    model, pairs = run_job("junit", KEY, Profile.STRICT, junit_xml())
    expected, expected_pairs = censor_junit(KEY, read_junit(junit_xml()))
    assert model == expected
    assert sorted(pairs.items()) == sorted(expected_pairs.items())


def test_isolated_junit_job_refuses_bad_xml() -> None:
    with pytest.raises(AsfbError) as raised:
        run_job("junit", KEY, Profile.STRICT, "<testsuite>")
    assert raised.value.code is ErrorCode.E_RUN_PARSE
