"""Log carriers: no output or error carries the canary, for every encoded form of it.

Every carrier is a valid log for each form of the canary (the forms are inserted as text, so none
of them may change the line structure). Refused sources are checked too: the error code and its
text carry nothing from the input.
"""

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.types.key import SessionKey
from tests.canary.harness import (
    Carrier,
    find_leaks,
    isolated_log_pipeline,
    load_log_carriers,
    log_format,
    log_pipeline,
    new_canary,
    plant,
    variants,
)

CASES = load_log_carriers()


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
            outputs = log_pipeline(plant(carrier.template, form), key, log_format(carrier.id))
        except AsfbError as error:
            errors = _error_texts(error)
        captured = capfd.readouterr()
        assert outputs, f"carrier refused, error codes scanned: {errors}"
        blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
        assert find_leaks(blobs, [canary]) == []


def test_refused_log_leaks_nothing(capfd: pytest.CaptureFixture[str]) -> None:
    canary = new_canary()
    text = "2024-05-01T10:00:00Z INFO " + canary + "x" * 70_000 + "\n"
    with pytest.raises(AsfbError) as info:
        log_pipeline(text, SessionKey.generate(), "text")
    assert info.value.code is ErrorCode.E_LOG_LINE_TOO_LONG
    captured = capfd.readouterr()
    assert (
        find_leaks([str(info.value), repr(info.value), captured.out, captured.err], [canary]) == []
    )


ISOLATED_CASES = list(enumerate(CASES))


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
    outputs: list[str] = []
    errors: list[str] = []
    try:
        outputs = isolated_log_pipeline(
            plant(carrier.template, form), SessionKey.generate(), log_format(carrier.id)
        )
    except AsfbError as error:
        errors = _error_texts(error)
    captured = capfd.readouterr()
    assert outputs, f"carrier refused, error codes scanned: {errors}"
    blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
    assert find_leaks(blobs, [canary]) == []
