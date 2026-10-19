"""Censored view forms (spec section 6). Documents come from the sanitizer, then render()."""

import json
import re

import pytest

from asfb import api, ingest
from asfb.egress import Emitted, emit, render
from asfb.errors import EgressError, ErrorCode
from asfb.sanitize.html import censor_document
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted
from asfb.vocab import FrozenVocabulary, html_registry

KEY = SessionKey(bytes(range(32)))
TOKEN_RE = re.compile(r"t_[0-9a-f]{16}")
LITERAL_RE = re.compile(r"DOCTYPE|cdata|pi|decl")
PUNCTUATION_RE = re.compile(r"[\s<>/\"'=!:?#&;-]*")
TERMS_RE = re.compile(
    "|".join(
        sorted(
            (
                re.escape(term)
                for vocab in html_registry().values()
                if isinstance(vocab, FrozenVocabulary)
                for term in vocab.terms
            ),
            key=len,
            reverse=True,
        )
    )
)


def _tok(text: str) -> str:
    return token_for(KEY, Untrusted(text))


def _view(html: str) -> str:
    return str(api.sanitize_html(html, key=KEY).views.censored())


def _leftover(text: str) -> str:
    text = TOKEN_RE.sub("", text)
    text = LITERAL_RE.sub("", text)
    return TERMS_RE.sub("", text)


def test_render_returns_the_censored_view() -> None:
    doc = api.sanitize_html("<p>x</p>", key=KEY)
    assert doc.views.censored() == render(doc.json)


def test_element_with_only_text_is_inline() -> None:
    assert _view("<h1>Welcome</h1>") == f"<h1>{_tok('Welcome')}</h1>"


def test_empty_element_is_inline_with_no_children() -> None:
    assert _view("<p></p>") == "<p></p>"


def test_nested_elements_are_indented_by_two_spaces() -> None:
    assert _view("<ul><li>a</li><li>b</li></ul>") == (
        f"<ul>\n  <li>{_tok('a')}</li>\n  <li>{_tok('b')}</li>\n</ul>"
    )


def test_text_next_to_elements_is_a_block_line() -> None:
    assert _view("<div>a<span>b</span></div>") == (
        f"<div>\n  {_tok('a')}\n  <span>{_tok('b')}</span>\n</div>"
    )


def test_void_element_has_no_end_tag() -> None:
    assert _view('<p><br><input type="email"></p>') == ('<p>\n  <br>\n  <input type="email">\n</p>')


def test_true_attribute_is_bare() -> None:
    assert _view("<input disabled>") == "<input disabled>"


def test_tuple_value_is_joined_with_spaces() -> None:
    assert _view('<button class="btn btn-primary">Go</button>') == (
        f'<button class="btn btn-primary">{_tok("Go")}</button>'
    )


def test_token_attribute_and_name_are_printed_as_tokens() -> None:
    assert _view('<input name="customer_email" value="ana">') == (
        f'<input name="{_tok("customer_email")}" value="{_tok("ana")}">'
    )


def test_full_url_is_reassembled() -> None:
    expected = (
        f'<a href="https://{_tok("h:1")}/{_tok("a")}/{_tok("b")}/?{_tok("q=1")}#{_tok("f")}">'
        f"{_tok('x')}</a>"
    )
    assert _view('<a href="https://h:1/a/b/?q=1#f">x</a>') == expected


def test_relative_url_keeps_its_shape() -> None:
    assert _view('<a href="docs/x/">y</a>') == (
        f'<a href="{_tok("docs")}/{_tok("x")}/">{_tok("y")}</a>'
    )


def test_rooted_url_keeps_its_leading_slash() -> None:
    assert _view('<a href="/invoices/42">y</a>') == (
        f'<a href="/{_tok("invoices")}/{_tok("42")}">{_tok("y")}</a>'
    )


def test_other_scheme_is_scheme_colon_and_remainder() -> None:
    assert _view('<a href="mailto:ana@example.com">m</a>') == (
        f'<a href="mailto:{_tok("ana@example.com")}">{_tok("m")}</a>'
    )


def test_html5_doctype_prints_the_html_marker() -> None:
    assert _view("<!DOCTYPE html><p>x</p>") == f"<!DOCTYPE html>\n<p>{_tok('x')}</p>"


def test_other_doctype_prints_the_bare_marker() -> None:
    assert _view('<!DOCTYPE html PUBLIC "x">') == "<!DOCTYPE>"


def test_comment_prints_as_token_in_comment_markers() -> None:
    assert _view("<!-- secret note -->") == f"<!-- {_tok('secret note')} -->"


@pytest.mark.parametrize(
    ("markup", "prefix"),
    [
        ("<![CDATA[raw]]>", "<!--cdata "),
        ("<?php echo 1?>", "<!--pi "),
    ],
)
def test_cdata_and_pi_are_marked_by_form(markup: str, prefix: str) -> None:
    text = _view(markup)
    assert text.startswith(prefix)
    assert text.endswith("-->")
    assert TOKEN_RE.fullmatch(text[len(prefix) : -len("-->")]) is not None


def test_declaration_is_marked_by_form() -> None:
    model = censor_document(KEY, (ingest.Declaration(Untrusted("if IE")),), Profile.STRICT)
    assert render(emit(model, vocab=html_registry())) == f"<!--decl {_tok('if IE')}-->"


def test_sample_document_renders_exactly() -> None:
    html = (
        '<!DOCTYPE html><html><body class="container"><form action="/invoices/42/send" '
        'method="post"><input type="email" name="customer_email" value="ana@example.com">'
        '<button class="btn btn-primary">Send invoice</button></form>'
        '<script>fetch("/api/x")</script></body></html>'
    )
    action = f"/{_tok('invoices')}/{_tok('42')}/{_tok('send')}"
    name = _tok("customer_email")
    value = _tok("ana@example.com")
    script = _tok('fetch("/api/x")')
    expected = "\n".join(
        [
            "<!DOCTYPE html>",
            "<html>",
            '  <body class="container">',
            f'    <form action="{action}" method="post">',
            f'      <input type="email" name="{name}" value="{value}">',
            f'      <button class="btn btn-primary">{_tok("Send invoice")}</button>',
            "    </form>",
            f"    <script>{script}</script>",
            "  </body>",
            "</html>",
        ]
    )
    assert _view(html) == expected


def test_output_holds_only_tokens_terms_and_literals() -> None:
    html = (
        '<!DOCTYPE html><div class="btn x-y" data-a="1"><!-- c --><![CDATA[d]]>'
        '<a href="https://h/p?q#f"></a><?pi z?><input disabled></div>'
    )
    assert PUNCTUATION_RE.fullmatch(_leftover(_view(html))) is not None


def test_canary_text_never_reaches_the_view() -> None:
    canary = "CANARY-ZQX-7731"
    view = _view(f'<div title="{canary}"><p>{canary}</p><!-- {canary} --></div>')
    assert canary not in view


def test_render_is_deterministic() -> None:
    doc = api.sanitize_html("<p>x<b>y</b></p>", key=KEY)
    assert render(doc.json) == render(doc.json)


def _reject(emitted: str) -> None:
    with pytest.raises(EgressError) as raised:
        render(Emitted(emitted))
    assert raised.value.code is ErrorCode.E_VIEW


@pytest.fixture
def sample() -> str:
    return str(api.sanitize_html("<div>x</div>", key=KEY).json)


def test_rejects_tampered_vocabulary_term(sample: str) -> None:
    _reject(sample.replace('"term":"div"', '"term":"zzz"'))


def test_rejects_undeclared_vocabulary(sample: str) -> None:
    _reject(sample.replace('"id":"html.elements"', '"id":"x.unknown"'))


def test_rejects_free_text_where_a_token_belongs(sample: str) -> None:
    _reject(re.sub(r"t_[0-9a-f]{16}", "hello", sample, count=1))


def test_rejects_non_canonical_json(sample: str) -> None:
    _reject(json.dumps(json.loads(sample), indent=2))


def test_rejects_non_json() -> None:
    _reject("<div>")
