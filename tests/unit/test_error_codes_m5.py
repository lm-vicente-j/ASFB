"""The codes added by the M5 batches exist and their values equal their names."""

import pytest

from asfb.errors import ErrorCode
from asfb.sandbox import protocol

M5_CODES = (
    "E_LOG_TOO_LARGE",
    "E_LOG_TOO_MANY_RECORDS",
    "E_LOG_LINE_TOO_LONG",
    "E_LOG_ENCODING_UNSUPPORTED",
    "E_LOG_TOO_MANY_FIELDS",
    "E_LOG_FIELD_NAME_TOO_LONG",
    "E_EDIT_UNSUPPORTED_KIND",
)
WORKER_LOG_CODES = tuple(name for name in M5_CODES if name.startswith("E_LOG_"))


@pytest.mark.parametrize("name", M5_CODES)
def test_code_exists_and_value_is_its_name(name: str) -> None:
    assert ErrorCode[name].value == name


@pytest.mark.parametrize("name", WORKER_LOG_CODES)
def test_worker_may_return_each_log_code(name: str) -> None:
    assert name in protocol.WORKER_CODES


def test_edit_refusal_is_not_a_worker_code() -> None:
    assert "E_EDIT_UNSUPPORTED_KIND" not in protocol.WORKER_CODES
