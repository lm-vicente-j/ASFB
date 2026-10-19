import io
import logging
from contextlib import redirect_stderr, redirect_stdout

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from asfb.types.key import SessionKey
from tests.canary.harness import (
    Carrier,
    find_leaks,
    html_pipeline,
    load_carriers,
    new_canary,
    plant,
)

CARRIERS = load_carriers()


class _RecordSink(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.mark.parametrize("carrier", CARRIERS, ids=lambda carrier: carrier.id)
def test_carrier_gives_zero_leaks(
    carrier: Carrier, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    canary = new_canary()
    key = SessionKey.generate()
    out = html_pipeline(f"<p>{plant(carrier.template, canary)}</p>", key)
    captured = capfd.readouterr()
    assert find_leaks([*out, caplog.text, captured.out, captured.err], [canary]) == []


@settings(deadline=None)
@given(
    raw=st.binary(min_size=8, max_size=8),
    key_bytes=st.binary(min_size=32, max_size=32),
    before=st.text(max_size=300),
    after=st.text(max_size=300),
)
def test_canary_at_random_offset_does_not_leak(
    raw: bytes, key_bytes: bytes, before: str, after: str
) -> None:
    canary = "cnry_" + raw.hex()
    sink = _RecordSink()
    stdout, stderr = io.StringIO(), io.StringIO()
    root = logging.getLogger()
    root.addHandler(sink)
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            out = html_pipeline(before + canary + after, SessionKey(key_bytes))
            print(out)  # noqa: T201
            logging.getLogger("asfb.canary").warning("emitted=%s", out)
    finally:
        root.removeHandler(sink)
    captured = [stdout.getvalue(), stderr.getvalue(), *sink.lines]
    assert find_leaks([*out, *captured], [canary]) == []
