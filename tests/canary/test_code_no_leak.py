"""Python carriers: no output or error carries the canary, in process and through the worker.

Every carrier is a valid stdlib token stream for each encoded form of the canary (the forms are
inserted as text, so none of them may break the lexer). The isolated test runs one worker per
carrier, one form each, since a worker costs a process start.
"""

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.types.key import SessionKey
from tests.canary.harness import (
    Carrier,
    code_pipeline,
    find_leaks,
    isolated_code_pipeline,
    load_code_carriers,
    new_canary,
    plant,
    variants,
)

CASES = load_code_carriers()
ISOLATED_CASES = list(enumerate(CASES))


def _error_texts(error: AsfbError) -> list[str]:
    return [
        str(error),
        repr(error),
        repr(error.args),
        *(str(arg) for arg in error.args),
        repr(error.__cause__),
        repr(error.__context__),
    ]


@pytest.mark.parametrize("carrier", CASES, ids=lambda carrier: carrier.id)
def test_carrier_gives_zero_leaks_in_process(
    carrier: Carrier, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    key = SessionKey.generate()
    for form in variants(canary):
        outputs: list[str] = []
        errors: list[str] = []
        try:
            outputs = code_pipeline(plant(carrier.template, form), key)
        except AsfbError as error:
            errors = _error_texts(error)
        captured = capfd.readouterr()
        assert outputs, f"carrier refused, error codes scanned: {errors}"
        blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
        assert find_leaks(blobs, [canary]) == []


@pytest.mark.parametrize(
    ("index", "carrier"), ISOLATED_CASES, ids=[carrier.id for _index, carrier in ISOLATED_CASES]
)
def test_carrier_gives_zero_leaks_isolated(
    index: int,
    carrier: Carrier,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    canary = new_canary()
    forms = variants(canary)
    form = forms[index % len(forms)]
    key = SessionKey.generate()
    outputs: list[str] = []
    errors: list[str] = []
    try:
        outputs = isolated_code_pipeline(plant(carrier.template, form), key)
    except AsfbError as error:
        errors = _error_texts(error)
    captured = capfd.readouterr()
    assert outputs, f"carrier refused, error codes scanned: {errors}"
    blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
    assert find_leaks(blobs, [canary]) == []


def test_refused_source_leaks_nothing(
    capfd: pytest.CaptureFixture[str],
) -> None:
    canary = new_canary()
    text = f"x = '\\u0041' + '{canary}\n"  # unterminated string: the lexer refuses it
    with pytest.raises(AsfbError) as info:
        code_pipeline(text, SessionKey.generate())
    assert info.value.code is ErrorCode.E_CODE_TOKENIZE_FAILED
    captured = capfd.readouterr()
    assert (
        find_leaks([str(info.value), repr(info.value), captured.out, captured.err], [canary]) == []
    )
