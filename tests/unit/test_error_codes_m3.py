"""The codes added by the M3-01 and M3-04 batches exist and their values equal their names."""

import pytest

from asfb.errors import ErrorCode

M3_CODES = ("E_TEXT_LINES", "E_MD_DEPTH", "E_MD_BLOCKS")


@pytest.mark.parametrize("name", M3_CODES)
def test_code_exists_and_value_is_its_name(name: str) -> None:
    assert ErrorCode[name].value == name
