"""HTML carriers through the isolated worker: outputs, errors and captured output carry no canary.

One worker process per carrier (about 1.5 s each), so each carrier gets one encoding of the
canary, chosen by index: the five forms rotate across the carriers. The in-process test covers
every form for every carrier. The worker's stderr is discarded by the client; the scan below
includes the parent's captured stdout and stderr to show that nothing from the child leaks.
"""

import pytest

from asfb.errors import AsfbError
from asfb.types.key import SessionKey
from tests.canary.harness import (
    Carrier,
    find_leaks,
    isolated_pipeline,
    load_html_carriers,
    new_canary,
    plant,
    variants,
)

HTML_CARRIERS = list(enumerate(load_html_carriers()))


def _error_texts(error: AsfbError) -> list[str]:
    return [
        str(error),
        repr(error),
        repr(error.args),
        *(str(arg) for arg in error.args),
        repr(error.__cause__),
        repr(error.__context__),
    ]


@pytest.mark.parametrize(
    ("index", "carrier"),
    HTML_CARRIERS,
    ids=[carrier.id for _index, carrier in HTML_CARRIERS],
)
def test_isolated_carrier_gives_zero_leaks(
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
        outputs = isolated_pipeline(plant(carrier.template, form), key)
    except AsfbError as error:
        errors = _error_texts(error)
    captured = capfd.readouterr()
    assert outputs, f"carrier refused, error codes scanned: {errors}"
    blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
    assert find_leaks(blobs, [canary]) == []
