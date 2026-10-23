import copy
import pickle
from typing import Any

import pytest

from asfb.errors import AsfbError, EgressError, ErrorCode, UntrustedAccessError

CANARY = "cnry_0123456789abcdef"
ERROR_CLASSES: list[type[AsfbError]] = [AsfbError, EgressError, UntrustedAccessError]


@pytest.mark.parametrize("code", list(ErrorCode))
def test_code_values_equal_names(code: ErrorCode) -> None:
    assert code.value == code.name


M1_CODES = {
    "E_SOURCE_UNSUPPORTED",
    "E_SOURCE_READ",
    "E_SOURCE_SIZE",
    "E_SOURCE_DECODE",
    "E_HTML_PARSE",
    "E_HTML_DEPTH",
    "E_HTML_NODES",
    "E_HTML_ATTRS",
    "E_VOCAB_DATA",
    "E_PROFILE",
    "E_PROFILE_KEY",
    "E_VIEW",
    "E_INTERNAL",
}


def test_m1_codes_are_defined() -> None:
    assert M1_CODES <= {code.value for code in ErrorCode}


B1_CODES = {
    "E_WORKER_START",
    "E_WORKER_TIMEOUT",
    "E_WORKER_LIMIT",
    "E_WORKER_PROTOCOL",
    "E_WORKER_PAIRS",
}


def test_b1_worker_codes_are_defined_with_their_names() -> None:
    values = {code.value for code in ErrorCode}
    assert B1_CODES <= values
    for name in B1_CODES:
        assert ErrorCode(name).value == name


def test_str_and_args_are_code_only() -> None:
    error = AsfbError(ErrorCode.E_TOKEN_ENCODING)
    assert str(error) == "E_TOKEN_ENCODING"
    assert error.args == ("E_TOKEN_ENCODING",)
    assert repr(error) == "AsfbError('E_TOKEN_ENCODING')"


def test_untrusted_access_error_is_type_error() -> None:
    error = UntrustedAccessError(ErrorCode.E_UNTRUSTED_ACCESS)
    assert isinstance(error, TypeError)
    assert isinstance(error, AsfbError)
    assert str(error) == "E_UNTRUSTED_ACCESS"
    assert error.args == ("E_UNTRUSTED_ACCESS",)


def test_egress_error_has_no_cause_or_context() -> None:
    with pytest.raises(EgressError) as info:
        raise EgressError(ErrorCode.E_EGRESS_TYPE)
    assert info.value.args == ("E_EGRESS_TYPE",)
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


@pytest.mark.parametrize("cls", ERROR_CLASSES, ids=lambda cls: cls.__name__)
def test_plain_string_code_is_refused(cls: type[AsfbError]) -> None:
    raw: Any = CANARY
    with pytest.raises(TypeError) as info:
        cls(raw)
    error = info.value
    assert str(error) == "AsfbError requires an ErrorCode"
    assert error.__cause__ is None
    assert error.__context__ is None
    for text in (str(error), repr(error), repr(error.args), repr(error.__cause__)):
        assert CANARY not in text


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
@pytest.mark.parametrize("cls", ERROR_CLASSES, ids=lambda cls: cls.__name__)
def test_pickle_round_trip(cls: type[AsfbError], protocol: int) -> None:
    original = cls(ErrorCode.E_EGRESS_TYPE)
    restored = pickle.loads(pickle.dumps(original, protocol=protocol))
    assert type(restored) is cls
    assert restored.code is ErrorCode.E_EGRESS_TYPE
    assert restored.args == original.args
    assert str(restored) == "E_EGRESS_TYPE"


@pytest.mark.parametrize("cls", ERROR_CLASSES, ids=lambda cls: cls.__name__)
def test_copy_round_trip(cls: type[AsfbError]) -> None:
    original = cls(ErrorCode.E_EGRESS_TYPE)
    for restored in (copy.copy(original), copy.deepcopy(original)):
        assert type(restored) is cls
        assert restored.code is ErrorCode.E_EGRESS_TYPE
        assert restored.args == original.args
        assert str(restored) == "E_EGRESS_TYPE"


B2_CODES = {
    "E_VAULT_FULL",
    "E_DOC_UNKNOWN",
    "E_PATH_OUTSIDE_ROOT",
    "E_PATH_UNKNOWN_TOKEN",
    "E_ROOT_INVALID",
    "E_LIST_DEPTH",
    "E_LIST_LIMIT",
}


def test_b2_vault_and_path_codes_are_defined_with_their_names() -> None:
    values = {code.value for code in ErrorCode}
    assert B2_CODES <= values
    for name in B2_CODES:
        assert ErrorCode(name).value == name


B3_CODES = {
    "E_EDIT_STALE",
    "E_EDIT_UNKNOWN_TOKEN",
    "E_EDIT_NO_MATCH",
    "E_EDIT_AMBIGUOUS",
    "E_EDIT_INVALID_TEXT",
    "E_EDIT_WRITE",
    "E_EDIT_TOO_LARGE",
}


def test_b3_edit_codes_are_defined_with_their_names() -> None:
    values = {code.value for code in ErrorCode}
    assert B3_CODES <= values
    for name in B3_CODES:
        assert ErrorCode(name).value == name
