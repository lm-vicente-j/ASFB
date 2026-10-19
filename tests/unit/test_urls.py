"""URL shapes (spec section 4, Url rule), checked through the sanitized href attribute."""

from typing import Any

import pytest

from asfb import api
from asfb.sanitize.tokens import token_for
from asfb.schema.html import Element, Url
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted
from asfb.types.values import EnumValue

KEY = SessionKey(bytes(range(32)))
MAX_PATH = 256


def _tok(text: str) -> Token:
    return token_for(KEY, Untrusted(text))


def _scheme(term: str) -> EnumValue:
    return EnumValue(vocab="url.schemes", term=term)


def _href(value: str) -> Any:
    doc = api.sanitize_html(f'<a href="{value}"></a>', key=KEY)
    element = doc.model.body[0]
    assert isinstance(element, Element)
    return element.attrs[0].value


def test_full_http_url_splits_every_part() -> None:
    assert _href("https://ana:pw@example.com:8443/a/b/?q=1#top") == Url(
        scheme=_scheme("https"),
        authority=_tok("ana:pw@example.com:8443"),
        rooted=True,
        path=(_tok("a"), _tok("b")),
        trailing_slash=True,
        query=_tok("q=1"),
        fragment=_tok("top"),
    )


def test_scheme_is_lowercased_before_lookup() -> None:
    assert _href("HTTP://Example.com/X") == Url(
        scheme=_scheme("http"),
        authority=_tok("Example.com"),
        rooted=True,
        path=(_tok("X"),),
    )


def test_scheme_less_relative_path_is_not_rooted() -> None:
    assert _href("docs/readme.md") == Url(path=(_tok("docs"), _tok("readme.md")))


def test_rooted_path_sets_rooted() -> None:
    assert _href("/invoices/42") == Url(rooted=True, path=(_tok("invoices"), _tok("42")))


def test_trailing_slash_is_a_flag_not_a_segment() -> None:
    assert _href("a/b/") == Url(path=(_tok("a"), _tok("b")), trailing_slash=True)


def test_root_alone_is_rooted_with_no_segments() -> None:
    assert _href("/") == Url(rooted=True)


def test_authority_only_is_scheme_less_with_authority() -> None:
    assert _href("//cdn.example.com") == Url(authority=_tok("cdn.example.com"))


def test_query_only_and_fragment_only() -> None:
    assert _href("?a=1") == Url(query=_tok("a=1"))
    assert _href("#top") == Url(fragment=_tok("top"))


def test_empty_segments_are_kept_as_tokens() -> None:
    assert _href("a//b") == Url(path=(_tok("a"), _tok(""), _tok("b")))


def test_ws_is_split_like_http() -> None:
    assert _href("wss://h/x") == Url(
        scheme=_scheme("wss"), authority=_tok("h"), rooted=True, path=(_tok("x"),)
    )


@pytest.mark.parametrize(
    ("value", "remainder"),
    [
        ("mailto:ana@example.com?subject=hi", "ana@example.com?subject=hi"),
        ("tel:+15551234", "+15551234"),
        ("javascript:alert(1)", "alert(1)"),
        ("data:text/plain;base64,AAAA", "text/plain;base64,AAAA"),
    ],
)
def test_other_scheme_remainder_is_one_token(value: str, remainder: str) -> None:
    scheme = value.partition(":")[0]
    assert _href(value) == Url(scheme=_scheme(scheme), path=(_tok(remainder),))


def test_other_scheme_with_empty_remainder_has_no_path() -> None:
    assert _href("mailto:") == Url(scheme=_scheme("mailto"))


def test_unknown_scheme_is_one_token_of_the_whole_value() -> None:
    assert _href("foo:bar/baz") == _tok("foo:bar/baz")


def test_urlsplit_failure_is_one_token_of_the_whole_value() -> None:
    assert _href("http://[::1/x") == _tok("http://[::1/x")


def test_empty_query_marker_is_one_token_of_the_whole_value() -> None:
    assert _href("/a?") == _tok("/a?")


def test_empty_fragment_marker_is_one_token_of_the_whole_value() -> None:
    assert _href("/a#") == _tok("/a#")


def test_dropped_authority_delimiter_is_one_token_of_the_whole_value() -> None:
    assert _href("file:///etc/x") == _tok("file:///etc/x")


def test_tab_inside_url_is_one_token_of_the_whole_value() -> None:
    assert _href("/a\tb") == _tok("/a\tb")


def test_path_of_exactly_256_segments_is_split() -> None:
    value = "/" + "/".join(["s"] * MAX_PATH)
    url = _href(value)
    assert isinstance(url, Url)
    assert len(url.path) == MAX_PATH


def test_path_over_256_segments_falls_back_to_one_token() -> None:
    value = "/" + "/".join(["s"] * (MAX_PATH + 1))
    assert _href(value) == _tok(value)
