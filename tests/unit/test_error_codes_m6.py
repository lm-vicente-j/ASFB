"""The codes added by the M6 batches exist and their values equal their names."""

import pytest

from asfb.errors import ErrorCode
from asfb.sandbox import protocol

M6_CODES = (
    "E_DB_NOT_SQLITE",
    "E_DB_TOO_LARGE",
    "E_DB_CORRUPT",
    "E_DB_UNSUPPORTED_RUNTIME",
    "E_DB_DENIED",
    "E_DB_BUDGET_EXCEEDED",
    "E_DB_LIMIT_EXCEEDED",
)


@pytest.mark.parametrize("name", M6_CODES)
def test_code_exists_and_value_is_its_name(name: str) -> None:
    assert ErrorCode[name].value == name


@pytest.mark.parametrize("name", M6_CODES)
def test_worker_may_return_each_db_code(name: str) -> None:
    assert name in protocol.WORKER_CODES


def test_db_codes_are_not_a_worker_reply_only_by_accident() -> None:
    assert protocol.WORKER_CODES >= frozenset(M6_CODES)
