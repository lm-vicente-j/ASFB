"""The codes added by the M4 batches exist and their values equal their names."""

import pytest

from asfb.errors import ErrorCode
from asfb.sandbox import protocol

M4_CODES = (
    "E_CODE_TOKENIZE_FAILED",
    "E_CODE_ENCODING_UNSUPPORTED",
    "E_CODE_TOO_LARGE",
    "E_CODE_LINE_TOO_LONG",
    "E_CODE_TOO_MANY_LEXEMES",
    "E_CODE_INDENT_TOO_DEEP",
)


@pytest.mark.parametrize("name", M4_CODES)
def test_code_exists_and_value_is_its_name(name: str) -> None:
    assert ErrorCode[name].value == name


@pytest.mark.parametrize("name", M4_CODES)
def test_worker_may_return_each_code(name: str) -> None:
    assert name in protocol.WORKER_CODES
