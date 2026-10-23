"""Database carriers: no output or error carries the canary, for every encoded form of it.

Each carrier is an SQL script that plants the canary as a name, a text of a view, trigger, check,
default, type, comment or row. The image is built in memory and only its bytes reach the reader.
Refused images are checked too: the error code and its text carry nothing from the input.
"""

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest.db import read_bytes
from asfb.types.key import SessionKey
from tests.canary.harness import (
    Carrier,
    db_file_pipeline,
    db_image,
    db_pipeline,
    find_leaks,
    load_db_carriers,
    new_canary,
    variants,
)
from tests.sqlite_images import image

CASES = load_db_carriers()


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
            outputs = db_pipeline(db_image(carrier.template, form), key)
        except AsfbError as error:
            errors = _error_texts(error)
        captured = capfd.readouterr()
        assert outputs, f"carrier refused, error codes scanned: {errors}"
        blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
        assert find_leaks(blobs, [canary]) == []


@pytest.mark.parametrize(
    ("index", "carrier"), list(enumerate(CASES)), ids=[carrier.id for carrier in CASES]
)
def test_carrier_gives_zero_leaks_through_the_public_file_path(
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
        outputs = db_file_pipeline(db_image(carrier.template, form), SessionKey.generate())
    except AsfbError as error:
        errors = _error_texts(error)
    captured = capfd.readouterr()
    assert outputs, f"carrier refused, error codes scanned: {errors}"
    blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
    assert find_leaks(blobs, [canary]) == []


def test_a_truncated_image_leaks_nothing(capfd: pytest.CaptureFixture[str]) -> None:
    canary = new_canary()
    script = f'CREATE TABLE "{canary}"(a TEXT); INSERT INTO "{canary}" VALUES (1);'  # noqa: S608
    data = image(script)
    with pytest.raises(AsfbError) as info:
        read_bytes(data[:700])
    assert info.value.code is ErrorCode.E_DB_CORRUPT
    captured = capfd.readouterr()
    assert (
        find_leaks([str(info.value), repr(info.value), captured.out, captured.err], [canary]) == []
    )


def test_a_not_sqlite_file_with_the_canary_leaks_nothing(capfd: pytest.CaptureFixture[str]) -> None:
    canary = new_canary()
    data = b"\x89PNG\r\n\x1a\n" + canary.encode("utf-8") * 8
    with pytest.raises(AsfbError) as info:
        read_bytes(data)
    assert info.value.code is ErrorCode.E_DB_NOT_SQLITE
    captured = capfd.readouterr()
    assert (
        find_leaks([str(info.value), repr(info.value), captured.out, captured.err], [canary]) == []
    )
