"""(token, surface) pairs from the in-process censoring. Every pair must recompute its token."""

import pickle
import re
from pathlib import Path

import pytest

from asfb import ingest, normalize
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize import html as sanitize_html
from asfb.sanitize.html import SurfacePairs, censor_document, censor_with_pairs
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted

KEY = SessionKey(bytes(range(32)))
README = Path(__file__).resolve().parents[1] / "corpus" / "html" / "readme_example.html"
TOKEN_RE = re.compile(r"t_[0-9a-f]{16}")


def _pairs(html: str) -> tuple[tuple[Token, str], ...]:
    _model, pairs = censor_with_pairs(KEY, ingest.read_text(html), Profile.STRICT)
    return pairs.items()


def _surfaces_of(html: str, token: Token) -> set[str]:
    return {surface for minted, surface in _pairs(html) if minted == token}


def test_every_pair_recomputes_its_token() -> None:
    html = README.read_text(encoding="utf-8")
    for token, surface in _pairs(html):
        assert token_for(KEY, Untrusted(surface)) == token


def test_every_token_in_the_document_has_a_pair() -> None:
    html = README.read_text(encoding="utf-8")
    model, pairs = censor_with_pairs(KEY, ingest.read_text(html), Profile.STRICT)
    minted = {token for token, _surface in pairs.items()}
    emitted = set(TOKEN_RE.findall(model.model_dump_json()))
    assert emitted <= minted


def test_censor_document_is_unchanged_by_the_pairs() -> None:
    nodes = ingest.read_text('<p title="a b">x <a href="/q?z=1">y</a></p>')
    with_pairs, _pairs_object = censor_with_pairs(KEY, nodes, Profile.STRICT)
    assert censor_document(KEY, nodes, Profile.STRICT) == with_pairs


def test_text_surface_keeps_inner_zero_width_characters() -> None:
    plain = _surfaces_of("<p>ab</p>", token_for(KEY, Untrusted("ab")))
    zero_width = _surfaces_of("<p>a​b</p>", token_for(KEY, Untrusted("ab")))
    assert plain == {"ab"}
    assert zero_width == {"a​b"}


def test_nfc_and_nfd_variants_are_distinct_surfaces_of_one_token() -> None:
    nfc = "café"
    nfd = "café"
    token = token_for(KEY, Untrusted(nfc))
    assert token == token_for(KEY, Untrusted(nfd))
    surfaces = _surfaces_of(f"<p>{nfc}</p><p>{nfd}</p>", token)
    assert surfaces == {nfc, nfd}


def test_outer_ascii_whitespace_is_stripped_from_the_surface() -> None:
    token = token_for(KEY, Untrusted("hi"))
    assert _surfaces_of("<p>  hi \n</p>", token) == {"hi"}


def test_control_characters_stay_in_the_surface_but_not_in_the_token() -> None:
    token = token_for(KEY, Untrusted("ab"))
    surfaces = _surfaces_of("<p>a\x01b</p>", token)
    assert surfaces == {"a\x01b"}
    assert token_for(KEY, Untrusted("a\x01b")) == token


def test_attribute_list_parts_get_their_raw_surfaces() -> None:
    token = token_for(KEY, Untrusted("b"))
    surfaces = _surfaces_of('<p class="a​x b">t</p>', token)
    assert "b" in surfaces
    for minted, surface in _pairs('<p class="a​x b">t</p>'):
        assert token_for(KEY, Untrusted(surface)) == minted


def test_url_pieces_are_reported() -> None:
    pairs = {surface for _token, surface in _pairs('<a href="https://ex.test/a/b?q=1#f">x</a>')}
    assert {"ex.test", "a", "b", "q=1", "f"} <= pairs


def test_fallback_url_token_is_reported_with_its_raw_surface() -> None:
    raw = "  zzz:bar  "
    pairs = _pairs(f'<a href="{raw}">x</a>')
    surfaces = {surface for _token, surface in pairs}
    assert raw.strip() in surfaces
    for token, surface in pairs:
        assert token_for(KEY, Untrusted(surface)) == token


def test_surface_pairs_repr_is_redacted_and_not_copyable() -> None:
    _model, pairs = censor_with_pairs(KEY, ingest.read_text("<p>secret-words</p>"), Profile.STRICT)
    assert "secret-words" not in repr(pairs)
    assert repr(pairs) == "SurfacePairs(<redacted>)"
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(pairs)
    with pytest.raises(TypeError, match="cannot be copied"):
        pairs.__copy__()
    assert isinstance(pairs, SurfacePairs)


def _surfaces(html: str) -> set[str]:
    return {surface for _token, surface in _pairs(html)}


def test_form_feed_in_a_class_value_keeps_the_raw_piece_and_aligns() -> None:
    # normalize.value drops FF (a control) before it splits, so the raw piece "a\x0cb" is the
    # surface of the normalized part "ab". Splitting the raw text on FF too would misalign.
    surfaces = _surfaces('<p class="a\x0cb c">x</p>')
    assert {"a\x0cb", "c"} <= surfaces


def test_zero_width_piece_in_a_class_value_gives_no_surface() -> None:
    surfaces = _surfaces('<p class="x \u200b y">x</p>')
    assert {"x", "y"} <= surfaces
    assert "\u200b" not in surfaces


def test_combining_mark_surface_stays_decomposed() -> None:
    surfaces = _surfaces('<p class="e\u0301 f">x</p>')
    assert "e\u0301" in surfaces
    assert "\u00e9" not in surfaces


@pytest.mark.parametrize(
    "value",
    ["a\x0cb c", "x \u200b y", "e\u0301 f", "  padded\t\r\n value ", "\x01a\x0bb\x0c c"],
)
def test_every_surface_is_a_substring_of_the_raw_value(value: str) -> None:
    for _token, surface in _pairs(f'<p class="{value}">x</p>'):
        if surface != "x":
            assert surface in value


def test_aligned_pairs_each_raw_piece_with_its_normalized_part() -> None:
    assert sanitize_html._aligned("a b", ["a", "b"]) == ["a", "b"]  # noqa: SLF001


def test_misaligned_pieces_are_refused_not_recorded() -> None:
    with pytest.raises(AsfbError) as info:
        sanitize_html._aligned("a b", ["a", "b", "c"])  # noqa: SLF001
    assert info.value.code is ErrorCode.E_HTML_PARSE


def test_a_split_that_breaks_the_invariant_fails_the_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_split = normalize.split
    extra = Untrusted("extra")

    def one_part_too_many(text: Untrusted[str], /) -> tuple[Untrusted[str], ...]:
        return (*real_split(text), extra)

    monkeypatch.setattr(normalize, "split", one_part_too_many)
    with pytest.raises(AsfbError) as info:
        censor_with_pairs(KEY, ingest.read_text('<p class="a b">x</p>'), Profile.STRICT)
    assert info.value.code is ErrorCode.E_HTML_PARSE
