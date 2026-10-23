"""The db job through the real worker process: run_job starts python -I -m asfb.sandbox.worker."""

import base64

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.sandbox.client import run_job
from asfb.sanitize.tokens import token_for
from asfb.schema.db import DbV1
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted
from tests.sqlite_images import SAMPLE_SCHEMA, image

KEY = SessionKey(bytes(range(32)))


def test_a_database_runs_in_a_worker_process() -> None:
    text = base64.b64encode(image(SAMPLE_SCHEMA)).decode("ascii")
    model, pairs = run_job("db", KEY, Profile.STRICT, text)
    assert isinstance(model, DbV1)
    assert len(model.tables) == 8  # the sample tables and the five shadow tables of fts5
    for token, surface in pairs.items():
        assert token_for(KEY, Untrusted(surface)) == token


def test_a_png_in_a_worker_process_gets_its_static_code() -> None:
    text = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode("ascii")
    with pytest.raises(AsfbError) as info:
        run_job("db", KEY, Profile.STRICT, text)
    assert info.value.code is ErrorCode.E_DB_NOT_SQLITE
