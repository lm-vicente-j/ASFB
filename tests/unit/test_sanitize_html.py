"""One test per row of the censoring table (spec section 4), on the sanitized model."""

from typing import Any

import pytest

from asfb import api, ingest
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize import html as censor
from asfb.sanitize.html import censor_document
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import DocV1, Profile, SourceKind
from asfb.schema.html import Doctype, Element, Node, Opaque, OpaqueForm, Text, Url
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted
from asfb.types.values import EnumValue
from asfb.vocab import FrozenVocabulary

KEY = SessionKey(bytes(range(32)))


def _html(doc: api.Doc) -> DocV1:
    assert isinstance(doc.model, DocV1)
    return doc.model


def _body(html: str) -> tuple[Node, ...]:
    return _html(api.sanitize_html(html, key=KEY)).body


def _only(html: str) -> Node:
    body = _body(html)
    assert len(body) == 1
    return body[0]


def _element(html: str) -> Element:
    node = _only(html)
    assert isinstance(node, Element)
    return node


def _attrs(html: str) -> dict[str, Any]:
    names: dict[str, Any] = {}
    for attr in _element(html).attrs:
        name = attr.name.term if isinstance(attr.name, EnumValue) else str(attr.name)
        names[name] = attr.value
    return names


def _tok(text: str) -> Token:
    return token_for(KEY, Untrusted(text))


def _term(vocab: str, term: str) -> EnumValue:
    return EnumValue(vocab=vocab, term=term)


def test_known_element_is_an_enum_value() -> None:
    assert _element("<div></div>").tag == _term("html.elements", "div")


def test_unknown_element_is_a_token() -> None:
    assert _element("<x-widget></x-widget>").tag == _tok("x-widget")


def test_svg_child_not_in_vocabulary_is_a_token() -> None:
    svg = _element("<svg><circle></circle></svg>")
    assert svg.tag == _term("html.elements", "svg")
    child = svg.children[0]
    assert isinstance(child, Element)
    assert child.tag == _tok("circle")


def test_known_attribute_name_is_an_enum_value() -> None:
    attr = _element('<a href="/x"></a>').attrs[0]
    assert attr.name == _term("html.attributes", "href")


def test_data_attribute_name_is_a_token_of_the_full_name() -> None:
    attr = _element('<div data-invoice-id="7"></div>').attrs[0]
    assert attr.name == _tok("data-invoice-id")
    assert attr.value == _tok("7")


def test_attribute_without_value_is_true() -> None:
    assert _attrs("<input disabled>")["disabled"] is True


def test_empty_attribute_value_is_true() -> None:
    assert _attrs('<input disabled="">')["disabled"] is True


def test_value_that_normalizes_to_empty_is_true() -> None:
    assert _attrs('<div title="​  ­"></div>')["title"] is True


def test_by_attribute_value_is_ascii_lowercased_and_listed_part_is_enum() -> None:
    assert _attrs('<input type="EMAIL">')["type"] == _term("html.values", "email")


def test_by_attribute_unlisted_value_is_a_token() -> None:
    assert _attrs('<input type="bogus">')["type"] == _tok("bogus")


def test_unlisted_attribute_part_is_the_token_of_its_original_case() -> None:
    attr = _attrs('<input type="Foo">')["type"]
    assert attr == _tok("Foo")
    assert _element("<p>Foo</p>").children == (Text(text=attr),)


@pytest.mark.parametrize("value", ["A", "a", "I", "i", "1"])
def test_ol_type_keeps_the_case_of_its_listed_term(value: str) -> None:
    assert _attrs(f'<ol type="{value}"></ol>')["type"] == _term("html.values", value)


def test_rel_mixes_listed_enum_and_unlisted_token_of_original_case() -> None:
    assert _attrs('<a rel="noopener Foo"></a>')["rel"] == (
        _term("html.values", "noopener"),
        _tok("Foo"),
    )


def test_by_attribute_list_mixes_enum_and_token_parts() -> None:
    value = _attrs('<form method="POST" enctype="multipart/form-data text/x">')
    assert value["method"] == _term("html.values", "post")
    assert value["enctype"] == (
        _term("html.values", "multipart/form-data"),
        _tok("text/x"),
    )


def test_by_attribute_element_entry_wins_over_star(monkeypatch: pytest.MonkeyPatch) -> None:
    real = censor._frozen  # noqa: SLF001
    values = real("html.values")
    fake = FrozenVocabulary(
        id="html.values",
        version=1,
        terms=values.terms,
        by_attribute={"*": {"dir": frozenset({"ltr"})}, "div": {"dir": frozenset({"rtl"})}},
    )

    def patched(vocab_id: str) -> FrozenVocabulary:
        return fake if vocab_id == "html.values" else real(vocab_id)

    monkeypatch.setattr(censor, "_frozen", patched)
    assert _attrs('<div dir="rtl ltr"></div>')["dir"] == (
        _term("html.values", "rtl"),
        _tok("ltr"),
    )
    assert _attrs('<p dir="ltr"></p>')["dir"] == _term("html.values", "ltr")
    assert _attrs('<div dir="ltr"></div>')["dir"] == _tok("ltr")


def test_class_exact_bootstrap_match_is_an_enum_value() -> None:
    assert _attrs('<div class="container"></div>')["class"] == _term("css.bootstrap", "container")


def test_class_is_case_sensitive_and_unknown_is_a_token() -> None:
    assert _attrs('<div class="BTN"></div>')["class"] == _tok("BTN")


def test_class_list_splits_on_ascii_whitespace() -> None:
    value = _attrs('<button class="btn\tbtn-primary  my-thing"></button>')["class"]
    assert value == (
        _term("css.bootstrap", "btn"),
        _term("css.bootstrap", "btn-primary"),
        _tok("my-thing"),
    )


def test_id_and_for_link_to_equal_tokens() -> None:
    label = _attrs('<label for="email-field"></label>')["for"]
    field = _attrs('<input id="email-field">')["id"]
    assert label == field == _tok("email-field")


def test_token_list_attributes_give_one_token_per_part() -> None:
    value = _attrs('<td headers="a  b"></td>')["headers"]
    assert value == (_tok("a"), _tok("b"))


def test_single_name_part_is_a_scalar_token() -> None:
    assert _attrs('<input name="customer_email">')["name"] == _tok("customer_email")


def test_aria_labelledby_is_a_token_list() -> None:
    value = _attrs('<div aria-labelledby="h1 h2"></div>')["aria-labelledby"]
    assert value == (_tok("h1"), _tok("h2"))


def test_url_attribute_is_a_url() -> None:
    value = _attrs('<a href="/invoices/42/send"></a>')["href"]
    assert isinstance(value, Url)
    assert value.rooted
    assert value.path == (_tok("invoices"), _tok("42"), _tok("send"))


def test_style_and_event_handler_are_one_token_of_the_whole_value() -> None:
    attrs = _attrs('<div style="color: red" onclick="fetch(\'/x\')"></div>')
    assert attrs["style"] == _tok("color: red")
    assert attrs["onclick"] == _tok("fetch('/x')")


def test_other_attribute_values_are_one_token_of_the_whole_value() -> None:
    assert _attrs('<img srcset="a.png 1x, b.png 2x">')["srcset"] == _tok("a.png 1x, b.png 2x")


def test_text_node_is_one_token() -> None:
    element = _element("<p>Send invoice now</p>")
    assert element.children == (Text(text=_tok("Send invoice now")),)


def test_whitespace_only_text_is_dropped() -> None:
    assert _element("<div> \n\t <span></span> \r\n </div>").children == (_element("<span></span>"),)


def test_zero_width_only_text_is_dropped() -> None:
    assert _element("<p>​‍</p>").children == ()


@pytest.mark.parametrize("tag", ["script", "style", "title", "textarea"])
def test_raw_text_element_content_is_one_token(tag: str) -> None:
    element = _element(f"<{tag}>  fetch('/api') \n x  </{tag}>")
    assert element.children == (Text(text=_tok("fetch('/api') \n x")),)


@pytest.mark.parametrize("tag", ["script", "style"])
def test_element_inside_raw_text_element_fails_closed(tag: str) -> None:
    # Built from the raw tree: html.parser may read the markup as text on some versions.
    inner = ingest.Element(tag=Untrusted("b"), attributes=(), children=())
    outer = ingest.Element(tag=Untrusted(tag), attributes=(), children=(inner,))
    with pytest.raises(AsfbError) as raised:
        censor_document(KEY, (outer,), Profile.STRICT)
    assert raised.value.code is ErrorCode.E_HTML_PARSE


@pytest.mark.parametrize("tag", ["title", "textarea"])
def test_element_inside_title_or_textarea_is_censored_as_a_node(tag: str) -> None:
    inner = ingest.Element(tag=Untrusted("b"), attributes=(), children=())
    outer = ingest.Element(tag=Untrusted(tag), attributes=(), children=(inner,))
    element = censor_document(KEY, (outer,), Profile.STRICT).body[0]
    assert isinstance(element, Element)
    assert element.children == (Element(tag=_term("html.elements", "b"), attrs=(), children=()),)


@pytest.mark.parametrize("tag", ["title", "textarea"])
def test_title_or_textarea_with_only_text_nodes_is_one_token(tag: str) -> None:
    # Raw tree with two Text children: the path is the single-token one, merged in order.
    pieces = (ingest.Text(Untrusted("a ")), ingest.Text(Untrusted("b")))
    outer = ingest.Element(tag=Untrusted(tag), attributes=(), children=pieces)
    element = censor_document(KEY, (outer,), Profile.STRICT).body[0]
    assert isinstance(element, Element)
    assert element.children == (Text(text=_tok("a b")),)


def test_comment_is_opaque_comment() -> None:
    assert _only("<!-- hello -->") == Opaque(form=OpaqueForm.COMMENT, text=_tok("hello"))


def test_cdata_is_opaque_cdata() -> None:
    assert _only("<![CDATA[raw text]]>") == Opaque(form=OpaqueForm.CDATA, text=_tok("raw text"))


def test_declaration_is_opaque_decl() -> None:
    # html.parser reads "<![if IE]>" as a comment on 3.14 and as a declaration on 3.12.
    node = ingest.Declaration(Untrusted("if IE"))
    body = censor_document(KEY, (node,), Profile.STRICT).body
    assert body == (Opaque(form=OpaqueForm.DECL, text=_tok("if IE")),)


def test_processing_instruction_is_opaque_pi() -> None:
    assert _only("<?xml version=1?>") == Opaque(form=OpaqueForm.PI, text=_tok("xml version=1?"))


@pytest.mark.parametrize("markup", ["<!DOCTYPE html>", "<!doctype HTML>", "<!DOCTYPE  html >"])
def test_html5_doctype_is_html_true(markup: str) -> None:
    assert _only(markup) == Doctype(html=True)


@pytest.mark.parametrize(
    "markup",
    [
        "<!DOCTYPE>",
        '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN">',
        "<!DOCTYPE htmlx>",
    ],
)
def test_other_doctype_is_html_false(markup: str) -> None:
    assert _only(markup) == Doctype(html=False)


def test_second_doctype_fails_closed() -> None:
    with pytest.raises(AsfbError) as raised:
        api.sanitize_html("<!DOCTYPE html><!DOCTYPE html>", key=KEY)
    assert raised.value.code is ErrorCode.E_HTML_PARSE


def test_entities_are_decoded_before_tokenizing() -> None:
    assert _element("<p>&amp;&#x41;</p>").children == (Text(text=_tok("&A")),)


def test_custom_element_with_odd_name_is_a_token() -> None:
    assert _element("<x-a_b.c></x-a_b.c>").tag == _tok("x-a_b.c")


def test_attribute_values_over_128_parts_fall_back_to_one_token() -> None:
    value = " ".join(["cls"] * 129)
    assert _attrs(f'<div aria-owns="{value}"></div>')["aria-owns"] == _tok(value)


def test_class_list_of_exactly_128_parts_is_a_tuple() -> None:
    value = " ".join(["cls"] * 128)
    assert len(_attrs(f'<div class="{value}"></div>')["class"]) == 128


def test_class_list_over_128_parts_falls_back_to_one_token() -> None:
    value = " ".join(["cls"] * 129)
    assert _attrs(f'<div class="{value}"></div>')["class"] == _tok(value)


@pytest.mark.parametrize(
    "markup", ['<p title="secretvalue>y</p><b>z</b>', '<p title="secretvalue>y</p><b>z</b>\n']
)
def test_unterminated_attribute_quote_drops_the_rest_without_leaking(markup: str) -> None:
    # Observed on 3.12 and 3.14 alike: the quote never closes, the open tag is dropped at EOF,
    # and nothing after the quote reaches the body. Only the common behavior is pinned.
    doc = api.sanitize_html(markup, key=KEY)
    assert _html(doc).body == ()
    assert str(doc.views.censored()) == ""
    assert "secretvalue" not in doc.json
    assert "secretvalue" not in str(doc.views.censored())


def test_document_declares_all_five_vocabularies_sorted() -> None:
    model = _html(api.sanitize_html("<p>x</p>", key=KEY))
    assert [ref.id for ref in model.vocab] == [
        "css.bootstrap",
        "html.attributes",
        "html.elements",
        "html.values",
        "url.schemes",
    ]
    assert all(ref.version == 1 for ref in model.vocab)
    assert model.source is SourceKind.HTML
    assert model.profile is Profile.STRICT
