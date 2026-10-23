"""Text carriers, and the M0 carriers used as plain text: no output or error carries the canary.

The in-process test covers every encoded form of the canary for every carrier. The isolated test
runs one worker per carrier with one form (the forms rotate by index), since a worker costs a
process start. Captured stdout and stderr of the test process are scanned too.
"""

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.types.key import SessionKey
from tests.canary.harness import (
    Carrier,
    find_leaks,
    isolated_text_pipeline,
    load_carriers,
    load_text_carriers,
    new_canary,
    plant,
    text_pipeline,
    variants,
)

TEXT_CARRIERS = load_text_carriers()
M0_AS_TEXT = [
    Carrier(id=f"m0-{carrier.id}", template=carrier.template) for carrier in load_carriers()
]
CASES = [*TEXT_CARRIERS, *M0_AS_TEXT]
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
            outputs = text_pipeline(plant(carrier.template, form), key)
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
        outputs = isolated_text_pipeline(plant(carrier.template, form), key)
    except AsfbError as error:
        errors = _error_texts(error)
    captured = capfd.readouterr()
    assert outputs, f"carrier refused, error codes scanned: {errors}"
    blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
    assert find_leaks(blobs, [canary]) == []


def test_too_many_lines_is_refused_without_the_canary(capfd: pytest.CaptureFixture[str]) -> None:
    canary = new_canary()
    text = f"{canary}\n" * 20_001
    with pytest.raises(AsfbError) as info:
        text_pipeline(text, SessionKey.generate())
    assert info.value.code is ErrorCode.E_TEXT_LINES
    captured = capfd.readouterr()
    assert (
        find_leaks([str(info.value), repr(info.value), captured.out, captured.err], [canary]) == []
    )
