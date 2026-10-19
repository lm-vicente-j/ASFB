from pathlib import Path
from typing import Any

import pytest

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import Element, Text, parse_html, read_path, read_text
from asfb.ingest import html as ingest_html
from asfb.ingest.limits import MAX_ATTRS, MAX_BYTES, MAX_DEPTH, MAX_NODES

CANARY = "cnry_0123456789abcdef"
VALID = "<p>x</p>"


def _code_of(call: Any, *args: Any) -> AsfbError:
    with pytest.raises(AsfbError) as info:
        call(*args)
    return info.value


def _assert_clean(error: AsfbError, code: ErrorCode) -> None:
    assert error.code is code
    assert error.args == (code.value,)
    assert str(error) == code.value
    assert repr(error) == f"AsfbError('{code.value}')"
    assert error.__cause__ is None
    assert error.__context__ is None


def _depth(nodes: tuple[Any, ...]) -> int:
    depth = 0
    current = nodes
    while current and isinstance(current[0], Element):
        depth += 1
        current = current[0].children
    return depth


def test_limits_have_the_specified_values() -> None:
    assert MAX_BYTES == 1_048_576
    assert MAX_DEPTH == 64
    assert MAX_NODES == 20_000
    assert MAX_ATTRS == 64


def test_text_at_max_bytes_is_accepted() -> None:
    (node,) = read_text("a" * MAX_BYTES)
    assert isinstance(node, Text)


def test_text_one_byte_over_max_bytes_is_refused() -> None:
    error = _code_of(read_text, "a" * (MAX_BYTES + 1))
    _assert_clean(error, ErrorCode.E_SOURCE_SIZE)


def test_max_bytes_counts_utf8_bytes_not_characters() -> None:
    two_byte = "é"
    assert len(read_text(two_byte * (MAX_BYTES // 2))) == 1
    error = _code_of(read_text, two_byte * (MAX_BYTES // 2 + 1))
    _assert_clean(error, ErrorCode.E_SOURCE_SIZE)


def test_file_at_max_bytes_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "big.html"
    path.write_bytes(b"a" * MAX_BYTES)
    assert len(read_path(path)) == 1


def test_file_one_byte_over_max_bytes_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "big.html"
    path.write_bytes(b"a" * (MAX_BYTES + 1))
    _assert_clean(_code_of(read_path, path), ErrorCode.E_SOURCE_SIZE)


def test_depth_at_max_is_accepted() -> None:
    nodes = parse_html("<div>" * MAX_DEPTH + "x" + "</div>" * MAX_DEPTH)
    assert _depth(nodes) == MAX_DEPTH


def test_depth_one_over_max_is_refused() -> None:
    html = "<div>" * (MAX_DEPTH + 1) + "x"
    _assert_clean(_code_of(read_text, html), ErrorCode.E_HTML_DEPTH)


def test_nodes_at_max_are_accepted() -> None:
    nodes = parse_html("<i></i>" * MAX_NODES)
    assert len(nodes) == MAX_NODES


def test_nodes_one_over_max_are_refused() -> None:
    _assert_clean(_code_of(read_text, "<i></i>" * (MAX_NODES + 1)), ErrorCode.E_HTML_NODES)


def test_attributes_at_max_are_accepted() -> None:
    attrs = " ".join(f"a{index}=x" for index in range(MAX_ATTRS))
    (node,) = parse_html(f"<p {attrs}></p>")
    assert isinstance(node, Element)
    assert len(node.attributes) == MAX_ATTRS


def test_attributes_one_over_max_are_refused() -> None:
    attrs = " ".join(f"a{index}=x" for index in range(MAX_ATTRS + 1))
    _assert_clean(_code_of(read_text, f"<p {attrs}></p>"), ErrorCode.E_HTML_ATTRS)


def test_duplicate_attributes_do_not_count_towards_the_limit() -> None:
    attrs = " ".join(["a=1"] * (MAX_ATTRS + 10))
    (node,) = parse_html(f"<p {attrs}></p>")
    assert isinstance(node, Element)
    assert len(node.attributes) == 1


@pytest.mark.parametrize("method", ["feed", "handle_comment"])
def test_parser_exception_maps_to_html_parse_without_chaining(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    def boom(self: object, *args: object) -> None:
        raise RuntimeError(CANARY)

    monkeypatch.setattr(ingest_html._Builder, method, boom)  # noqa: SLF001
    error = _code_of(parse_html, "<p>x<!--c--></p>")
    _assert_clean(error, ErrorCode.E_HTML_PARSE)
    for text in (str(error), repr(error), repr(error.args)):
        assert CANARY not in text


def test_parser_exception_through_read_text_is_html_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: object, *args: object) -> None:
        raise ValueError(CANARY)

    monkeypatch.setattr(ingest_html._Builder, "feed", boom)  # noqa: SLF001
    _assert_clean(_code_of(read_text, VALID), ErrorCode.E_HTML_PARSE)


def test_invalid_utf8_file_gives_decode_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.html"
    path.write_bytes(b"<p>\xc3</p>")
    _assert_clean(_code_of(read_path, path), ErrorCode.E_SOURCE_DECODE)


def test_utf8_bom_is_accepted_in_files(tmp_path: Path) -> None:
    path = tmp_path / "bom.html"
    path.write_bytes(b"\xef\xbb\xbf<p>x</p>")
    (node,) = read_path(path)
    assert isinstance(node, Element)


def test_lone_surrogate_in_string_gives_decode_error() -> None:
    _assert_clean(_code_of(read_text, "\ud800"), ErrorCode.E_SOURCE_DECODE)


@pytest.mark.parametrize("name", ["page.txt", "page.xhtml", "page.php", "page"])
def test_wrong_suffix_is_unsupported(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text(VALID, encoding="utf-8")
    _assert_clean(_code_of(read_path, path), ErrorCode.E_SOURCE_UNSUPPORTED)


@pytest.mark.parametrize("name", ["page.html", "page.htm", "PAGE.HTML"])
def test_html_suffixes_are_accepted(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text(VALID, encoding="utf-8")
    assert len(read_path(path)) == 1


def test_non_path_argument_is_unsupported() -> None:
    raw: Any = b"page.html"
    _assert_clean(_code_of(read_path, raw), ErrorCode.E_SOURCE_UNSUPPORTED)


def test_non_str_text_argument_is_unsupported() -> None:
    raw: Any = b"<p>x</p>"
    _assert_clean(_code_of(read_text, raw), ErrorCode.E_SOURCE_UNSUPPORTED)


def test_missing_file_gives_read_error_without_path_text(tmp_path: Path) -> None:
    path = tmp_path / "secret-name-canary.html"
    error = _code_of(read_path, path)
    _assert_clean(error, ErrorCode.E_SOURCE_READ)
    for text in (str(error), repr(error), repr(error.args)):
        assert "secret-name" not in text
        assert str(tmp_path) not in text


def test_directory_with_html_suffix_gives_read_error(tmp_path: Path) -> None:
    path = tmp_path / "folder.html"
    path.mkdir()
    _assert_clean(_code_of(read_path, path), ErrorCode.E_SOURCE_READ)


def test_nul_in_path_gives_read_error() -> None:
    _assert_clean(_code_of(read_path, "bad\x00name.html"), ErrorCode.E_SOURCE_READ)
