import io
import logging
import string
from contextlib import redirect_stderr, redirect_stdout

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from asfb.errors import AsfbError
from asfb.types.key import SessionKey
from tests.canary.harness import (
    Carrier,
    find_leaks,
    html_pipeline,
    load_carriers,
    load_html_carriers,
    new_canary,
    plant,
    variants,
)

HTML_CARRIERS = load_html_carriers()
M0_WRAPPED = [
    Carrier(id=f"m0-{carrier.id}", template=f"<p>{carrier.template}</p>")
    for carrier in load_carriers()
]
CASES = [*HTML_CARRIERS, *M0_WRAPPED]

_FRAGMENTS = st.sampled_from(
    [
        "<p>",
        "</p>",
        '<a href="',
        '">',
        "<script>",
        "</script>",
        "<style>",
        "</style>",
        "<!--",
        "-->",
        "<![CDATA[",
        "]]>",
        "<?",
        "?>",
        "<!DOCTYPE ",
        "&amp;",
        "&#",
        ";",
        "=",
        " ",
        "'",
        '"',
        "<x-",
        ">",
        "/",
        "#",
        "?",
        "btn btn-primary",
        "form",
        "data-",
    ]
)
_HTMLISH = st.lists(
    st.one_of(
        st.text(alphabet=string.ascii_letters + "<>/=\"'!-?&;# \n", max_size=6),
        _FRAGMENTS,
    ),
    max_size=40,
).map("".join)


class _RecordSink(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def _error_texts(error: AsfbError) -> list[str]:
    return [
        str(error),
        repr(error),
        repr(error.args),
        *(str(arg) for arg in error.args),
        repr(error.__cause__),
        repr(error.__context__),
    ]


def _outcome(html: str, key: SessionKey) -> tuple[list[str], list[str]]:
    """Returns (outputs, error texts). Exactly one of the two is empty."""
    try:
        return html_pipeline(html, key), []
    except AsfbError as error:
        return [], _error_texts(error)


@pytest.mark.parametrize("carrier", CASES, ids=lambda carrier: carrier.id)
def test_carrier_gives_zero_leaks(
    carrier: Carrier, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    key = SessionKey.generate()
    for form in variants(canary):
        outputs, errors = _outcome(plant(carrier.template, form), key)
        captured = capfd.readouterr()
        assert outputs, f"carrier refused, error codes scanned: {errors}"
        blobs = [*outputs, *errors, caplog.text, captured.out, captured.err]
        assert find_leaks(blobs, [canary]) == []


@settings(deadline=None)
@given(
    raw=st.binary(min_size=8, max_size=8),
    key_bytes=st.binary(min_size=32, max_size=32),
    before=_HTMLISH,
    after=_HTMLISH,
)
def test_canary_at_random_offset_in_html_does_not_leak(
    raw: bytes, key_bytes: bytes, before: str, after: str
) -> None:
    canary = "cnry_" + raw.hex()
    sink = _RecordSink()
    stdout, stderr = io.StringIO(), io.StringIO()
    root = logging.getLogger()
    root.addHandler(sink)
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            outputs, errors = _outcome(before + canary + after, SessionKey(key_bytes))
            print(outputs, errors)  # noqa: T201
            logging.getLogger("asfb.canary").warning("outputs=%s", outputs)
    finally:
        root.removeHandler(sink)
    captured = [stdout.getvalue(), stderr.getvalue(), *sink.lines]
    assert find_leaks([*outputs, *errors, *captured], [canary]) == []
