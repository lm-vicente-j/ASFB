"""The codes added by the M2-11..M2-15 batches exist and their values equal their names."""

import pytest

from asfb.errors import ErrorCode

M2_CODES = (
    "E_RUN_PARSE",
    "E_RUN_TOOL",
    "E_RUN_TARGETS",
    "E_RUN_NETWORK",
    "E_RUN_START",
    "E_RUN_CONFIG",
    "E_USAGE",
    "E_MCP_ARGS",
)


@pytest.mark.parametrize("name", M2_CODES)
def test_code_exists_and_value_is_its_name(name: str) -> None:
    assert ErrorCode[name].value == name
