import re
import string
import unicodedata

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from asfb import sanitize_html
from asfb.errors import AsfbError, ErrorCode
from asfb.types.key import SessionKey
from asfb.vocab import FrozenVocabulary, html_registry
from tests.canary.harness import find_leaks

_TOKEN = re.compile(r"t_[0-9a-f]{16}")
_RUN = re.compile(r"[A-Za-z0-9_.:/+-]+")
_LITERALS = frozenset({"DOCTYPE", "cdata", "pi", "decl"})
_SYNTAX = frozenset('<>="!?# \n')
_ZERO_WIDTH = ["​", "‌", "‍", "⁠", "﻿", "­", "‎", "‮", ""]
_WORD = st.text(alphabet=string.ascii_letters, min_size=1, max_size=16).map(lambda s: "zq" + s)
_LETTERS = st.text(alphabet="abcdeéèñüAB", min_size=1, max_size=20)
_HTML_TEXT = st.text(max_size=400)
_HTMLISH = st.lists(
    st.one_of(
        st.text(alphabet=string.ascii_letters + "<>/=\"'!-?&;# \n", max_size=6),
        st.sampled_from(
            ["<p>", "</p>", '<a href="', '">', "<script>", "</script>", "<!--", "-->", "<?", "?>"]
        ),
    ),
    max_size=40,
).map("".join)
_TAGS = st.sampled_from(
    ["div", "p", "a", "span", "form", "input", "img", "script", "style", "svg", "x-widget", "label"]
)
_ATTRS = st.sampled_from(
    ["id", "class", "href", "src", "type", "for", "name", "value", "data-x", "style", "onclick"]
)
_VALUES = st.one_of(
    _WORD,
    st.sampled_from(
        [
            "btn btn-primary",
            "text",
            "email",
            "/invoices/send",
            "https://example.com/a?b=1#c",
            "mailto:a@b.c",
            "javascript:go()",
            "post",
            "_blank",
            "true",
            "menu",
            "1.5 2.5",
        ]
    ),
)
_KEYS = st.binary(min_size=32, max_size=32)


def _terms() -> frozenset[str]:
    terms: set[str] = set()
    for vocab in html_registry().values():
        if isinstance(vocab, FrozenVocabulary):
            terms |= vocab.terms
            for scopes in vocab.by_attribute.values():
                for values in scopes.values():
                    terms |= values
    return frozenset(terms)


_VOCAB_TERMS: frozenset[str] = _terms()


@st.composite
def _soup(draw: st.DrawFn) -> str:
    pieces: list[str] = []
    for _ in range(draw(st.integers(min_value=1, max_value=8))):
        if draw(st.booleans()):
            pieces.append(f"<!--{draw(_WORD)}-->")
            continue
        tag = draw(_TAGS)
        attrs = "".join(
            f' {draw(_ATTRS)}="{draw(_VALUES)}"'
            for _ in range(draw(st.integers(min_value=0, max_value=3)))
        )
        pieces.append(f"<{tag}{attrs}>{draw(_WORD)}</{tag}>")
    return "".join(pieces)


def _outcome(html: str, key: SessionKey) -> tuple[str, str] | ErrorCode:
    try:
        doc = sanitize_html(html, key=key)
    except AsfbError as error:
        return error.code
    return str(doc.json), str(doc.views.censored())


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text))


def _view(html: str, key: SessionKey) -> str:
    outcome = _outcome(html, key)
    assert isinstance(outcome, tuple)
    return outcome[1]


def _assert_only_allowed(view: str) -> None:
    rest = _TOKEN.sub(" ", view)
    for run in _RUN.findall(rest):
        if run in _VOCAB_TERMS or run in _LITERALS:
            continue
        pieces = [piece for piece in re.split(r"[/:]", run) if piece]
        for piece in pieces:
            assert piece in _VOCAB_TERMS or piece in _LITERALS or set(piece) <= {"-"}, piece
    for char in _RUN.sub(" ", rest):
        assert char in _SYNTAX, repr(char)


@settings(deadline=None, max_examples=200)
@given(text=st.one_of(_HTML_TEXT, _HTMLISH))
def test_any_text_gives_a_doc_or_a_static_error(text: str) -> None:
    outcome = _outcome(text, SessionKey.generate())
    if isinstance(outcome, ErrorCode):
        assert outcome is not ErrorCode.E_INTERNAL
    else:
        assert outcome[0]


@settings(deadline=None)
@given(
    key_bytes=_KEYS,
    raw=st.binary(min_size=8, max_size=8),
    before=_soup(),
    after=_soup(),
    wrapper=st.sampled_from(
        [
            "<p>{}</p>",
            '<a href="/x/{}">x</a>',
            '<p title="{}">x</p>',
            "<script>{}</script>",
            "<!-- {} -->",
            "<x-{}>y</x-{}>",
            "<style>.{}{{}}</style>",
            '<p data-{}="v">x</p>',
        ]
    ),
)
def test_generated_html_with_canary_never_leaks(
    key_bytes: bytes, raw: bytes, before: str, after: str, wrapper: str
) -> None:
    canary = "cnry_" + raw.hex()
    html = before + wrapper.replace("{}", canary) + after
    outcome = _outcome(html, SessionKey(key_bytes))
    blobs = list(outcome) if isinstance(outcome, tuple) else [outcome.value]
    assert find_leaks(blobs, [canary]) == []


@settings(deadline=None)
@given(key_bytes=_KEYS, html=_soup())
def test_same_key_gives_same_output(key_bytes: bytes, html: str) -> None:
    key = SessionKey(key_bytes)
    assert _outcome(html, key) == _outcome(html, key)


@settings(deadline=None)
@given(
    first=_KEYS,
    second=_KEYS,
    value=_WORD,
)
def test_different_keys_give_different_tokens(first: bytes, second: bytes, value: str) -> None:
    assume(first != second)
    one = _outcome(f"<p>{value}</p>", SessionKey(first))
    other = _outcome(f"<p>{value}</p>", SessionKey(second))
    assert isinstance(one, tuple)
    assert isinstance(other, tuple)
    assert _tokens(one[0])
    assert _tokens(one[0]).isdisjoint(_tokens(other[0]))


@settings(deadline=None)
@given(key_bytes=_KEYS, word=_LETTERS, data=st.data())
def test_nfc_nfd_and_zero_width_variants_share_a_token(
    key_bytes: bytes, word: str, data: st.DataObject
) -> None:
    key = SessionKey(key_bytes)
    nfd = unicodedata.normalize("NFD", word)
    padded = "".join(char + data.draw(st.sampled_from(_ZERO_WIDTH)) for char in nfd)
    views = {_view(f"<p>{variant}</p>", key) for variant in (word, nfd, padded)}
    assert len(views) == 1


@settings(deadline=None)
@given(
    key_bytes=_KEYS,
    value=st.text(alphabet=string.ascii_letters + "-_", min_size=1, max_size=20),
)
def test_id_and_for_of_the_same_value_share_a_token(key_bytes: bytes, value: str) -> None:
    view = _view(f'<label for="{value}">a</label><input id="{value}">', SessionKey(key_bytes))
    fors = re.findall(r'for="([^"]*)"', view)
    ids = re.findall(r' id="([^"]*)"', view)
    assert len(fors) == 1
    assert fors == ids


@settings(deadline=None)
@given(key_bytes=_KEYS, html=_soup())
def test_censored_view_has_only_tokens_terms_and_tag_syntax(key_bytes: bytes, html: str) -> None:
    _assert_only_allowed(_view(html, SessionKey(key_bytes)))
