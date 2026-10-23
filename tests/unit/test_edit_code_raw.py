"""Lexeme edits of a Python file are made on its raw text: bytes outside the edited range survive
exactly (CRLF, dropped Cf and Cc controls, a BOM), and a range that touches or splits a dropped
control is refused."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from asfb import normalize
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.tokens import token_for
from asfb.session import Sanitized, Session
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted

KEY = SessionKey(bytes(range(32)))
ZW = "​"  # Cf, dropped by the normalizer
ESC = "\x1b"  # Cc, dropped by the normalizer


def _token(surface: str) -> str:
    return str(token_for(KEY, Untrusted(surface)))


def _edit(root: Path, name: str, old: str, new: str) -> ErrorCode | None:
    session = Session([str(root)], key=KEY)
    result = session.sanitize(name)
    assert isinstance(result, Sanitized)
    try:
        session.edit(str(result.ref), old, new)
    except AsfbError as error:
        return error.code
    return None


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


def _write(root: Path, data: str) -> Path:
    path = root / "mod.py"
    path.write_bytes(data.encode("utf-8"))
    return path


def test_crlf_and_dropped_controls_outside_the_range_survive_byte_for_byte(project: Path) -> None:
    source = (
        f"def ingest(payload):\r\n"
        f"    return payload  # note{ZW}{ESC}\r\n"
        f"\r\n"
        f"\r\n"
        f"VALUE = 'secret'\r\n"
    )
    path = _write(project, source)
    old = f"return {_token('payload')}"
    new = f"return {_token('payload')} + 1"
    assert _edit(project, "mod.py", old, new) is None
    expected = source.replace("return payload  #", "return payload + 1  #")
    assert path.read_bytes() == expected.encode("utf-8")


def test_a_bom_is_kept(project: Path) -> None:
    source = "def ingest(payload):\r\n    return payload\r\n"
    path = _write(project, source)
    raw = b"\xef\xbb\xbf" + source.encode("utf-8")
    path.write_bytes(raw)
    old = f"return {_token('payload')}"
    assert _edit(project, "mod.py", old, f"return {_token('payload')} + 1") is None
    assert path.read_bytes() == b"\xef\xbb\xbf" + source.replace(
        "return payload", "return payload + 1"
    ).encode("utf-8")


def test_a_range_across_crlf_takes_the_file_newlines(project: Path) -> None:
    source = "def ingest(payload):\r\n    return payload\r\n\r\nVALUE = 1\r\n"
    path = _write(project, source)
    old = f"def ingest(payload):\n    return {_token('payload')}"
    new = f"def ingest(payload):\n    return {_token('payload')} + 1"
    assert _edit(project, "mod.py", old, new) is None
    assert path.read_bytes() == source.replace(
        "    return payload\r\n", "    return payload + 1\r\n", 1
    ).encode("utf-8")


def test_mixed_newlines_outside_the_range_survive(project: Path) -> None:
    source = "# top\r\ndef ingest(payload):\n    return payload\r\n\nVALUE = 1\r\n"
    path = _write(project, source)
    old = f"return {_token('payload')}"
    assert _edit(project, "mod.py", old, f"return {_token('payload')} + 1") is None
    assert path.read_bytes() == source.replace("return payload", "return payload + 1").encode()


def test_a_range_that_splits_a_dropped_control_is_refused(project: Path) -> None:
    source = f"VALUE = 'sec{ZW}ret'\r\n"
    path = _write(project, source)
    old = f"VALUE = {_token(chr(39) + 'secret' + chr(39))}"
    assert _edit(project, "mod.py", old, "VALUE = 'other'") is ErrorCode.E_EDIT_CONTROL_CHARACTER
    assert path.read_bytes() == source.encode("utf-8")


def test_a_range_that_touches_a_dropped_control_is_refused(project: Path) -> None:
    source = f"def ingest(payload):\r\n    return payload{ZW}\r\n"
    path = _write(project, source)
    old = f"return {_token('payload')}"
    new = f"return {_token('payload')} + 1"
    assert _edit(project, "mod.py", old, new) is ErrorCode.E_EDIT_CONTROL_CHARACTER
    assert path.read_bytes() == source.encode("utf-8")


def test_a_range_touching_a_control_before_it_is_refused(project: Path) -> None:
    source = f"def ingest(payload):\r\n    return{ZW} payload\r\n"
    path = _write(project, source)
    old = f"return {_token('payload')}"
    assert _edit(project, "mod.py", old, f"return {_token('payload')} + 1") is (
        ErrorCode.E_EDIT_CONTROL_CHARACTER
    )
    assert path.read_bytes() == source.encode("utf-8")


def test_code_range_maps_to_raw_offsets_and_refuses_controls() -> None:
    raw = f"a\r\nb{ZW}c\rd"
    normalized, spans = normalize.code_spans(raw)
    assert normalized == "a\nbc\nd"
    assert normalize.code_raw_range(raw, spans, 0, 1) == (0, 1)
    assert normalize.code_raw_range(raw, spans, 1, 2) == (1, 3)  # the CRLF pair as one LF
    assert normalize.code_raw_range(raw, spans, 2, 4) is None  # bc splits ZW
    assert normalize.code_raw_range(raw, spans, 3, 4) is None  # c touches ZW before it
    assert normalize.code_raw_range(raw, spans, 4, 5) == (6, 7)  # the lone CR
    assert normalize.code_raw_range(raw, spans, 5, 5) is None


_PIECES = st.sampled_from(["a", "b", " ", "\r", "\n", "\r\n", "\t", ZW, ESC, "\x0c"])


@settings(max_examples=300, deadline=None)
@given(
    pieces=st.lists(_PIECES, max_size=12),
    bounds=st.tuples(st.integers(0, 12), st.integers(0, 12)),
)
def test_mapped_ranges_normalize_to_the_normalized_range(
    pieces: list[str], bounds: tuple[int, int]
) -> None:
    raw = "".join(pieces)
    normalized, spans = normalize.code_spans(raw)
    assert normalized == normalize.code_text(raw)[0]
    start, end = sorted(bounds)
    if start >= end or end > len(normalized):
        return
    mapped = normalize.code_raw_range(raw, spans, start, end)
    if mapped is None:
        return
    low, high = mapped
    assert normalize.code_text(raw[low:high])[0] == normalized[start:end]


def test_an_inconsistent_mapping_is_refused_as_ambiguous(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "def ingest(payload):\r\n    return payload\r\n"
    path = _write(project, source)
    real_range = normalize.code_raw_range

    def shifted(
        raw: str, spans: tuple[tuple[int, int], ...], start: int, end: int
    ) -> tuple[int, int] | None:
        mapped = real_range(raw, spans, start, end)
        return None if mapped is None else (mapped[0], mapped[1] - 1)

    monkeypatch.setattr(normalize, "code_raw_range", shifted)
    old = f"return {_token('payload')}"
    assert _edit(project, "mod.py", old, f"return {_token('payload')} + 1") is (
        ErrorCode.E_EDIT_AMBIGUOUS
    )
    assert path.read_bytes() == source.encode("utf-8")
