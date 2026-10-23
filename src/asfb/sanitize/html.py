"""HTML censoring: the raw ingest tree becomes schema nodes, one token per user string.

Vocabulary terms pass through as EnumValue. Every other string from the source becomes a Token
minted under the session key. Constructs the censoring table does not cover fail closed with
E_HTML_PARSE. Strings are read here (unwrap is allowed in sanitize/), never copied out.

Every token minted here is also reported as a (token, surface) pair. A surface is the decoded
value with outer ASCII whitespace stripped, taken before Cf removal and NFC, so variants that
normalize to one token stay distinct surfaces. Pairs hold originals: they go only to the vault
(through the isolated worker or in-process) and are never part of a Doc.
"""

import re
import string
from collections.abc import Container
from typing import Final

from asfb import ingest, normalize
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.pairs import PairRecorder, surface_of
from asfb.sanitize.pairs import SurfacePairs as SurfacePairs  # re-exported for vault and tests
from asfb.sanitize.tokens import token_for
from asfb.sanitize.urls import censor_url
from asfb.schema import html as schema
from asfb.schema.doc import DocV1, Profile, SourceKind, VocabRef
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted, unwrap
from asfb.types.values import EnumValue
from asfb.vocab import FrozenVocabulary, html_registry

HTML_ELEMENTS: Final = "html.elements"
HTML_ATTRIBUTES: Final = "html.attributes"
HTML_VALUES: Final = "html.values"
CSS_BOOTSTRAP: Final = "css.bootstrap"
URL_SCHEMES: Final = "url.schemes"
_TOKEN_LIST: Final = frozenset(
    {
        "id",
        "name",
        "for",
        "form",
        "list",
        "headers",
        "itemref",
        "aria-labelledby",
        "aria-describedby",
        "aria-controls",
        "aria-owns",
    }
)
_URL_ATTRIBUTES: Final = frozenset(
    {"href", "src", "action", "formaction", "cite", "poster", "data", "background"}
)
_RAW_TEXT: Final = frozenset({"script", "style"})
_TEXT_ONLY: Final = frozenset({"title", "textarea"})
_MAX_PARTS: Final = 128
_LOWER: Final = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_RAW_SPLIT: Final = re.compile(r"[ \t\n\r]+")


def _ascii_lower(text: str) -> str:
    return text.translate(_LOWER)


def _aligned(raw: str, parts: list[str]) -> list[str]:
    """One raw surface per normalized part. The raw pieces split on the same ASCII whitespace as
    normalize.split, so they must normalize to exactly the parts. If they do not, the invariant is
    broken and the document is refused: a normalized value is never recorded as a surface."""
    candidates = [surface_of(piece) for piece in _RAW_SPLIT.split(raw)]
    surfaces = [
        candidate for candidate in candidates if unwrap(normalize.value(Untrusted(candidate)))
    ]
    if [unwrap(normalize.value(Untrusted(surface))) for surface in surfaces] != parts:
        raise AsfbError(ErrorCode.E_HTML_PARSE)
    return surfaces


def _frozen(vocab_id: str) -> FrozenVocabulary:
    entry = html_registry()[vocab_id]
    if not isinstance(entry, FrozenVocabulary):
        raise AsfbError(ErrorCode.E_VOCAB_DATA)
    return entry


def _declared() -> tuple[VocabRef, ...]:
    registry = html_registry()
    return tuple(
        VocabRef(id=registry[vocab_id].id, version=registry[vocab_id].version)
        for vocab_id in sorted(registry)
    )


def _term(vocab_id: str, term: str) -> EnumValue:
    return EnumValue(vocab=vocab_id, term=term)


class _Censor:
    """Walks one raw tree. Holds the session key, the vocabularies and the pairs, nothing else."""

    def __init__(self, key: SessionKey) -> None:
        self._session = key
        self._elements = _frozen(HTML_ELEMENTS)
        self._attributes = _frozen(HTML_ATTRIBUTES)
        self._values = _frozen(HTML_VALUES)
        self._classes = _frozen(CSS_BOOTSTRAP)
        self._schemes = _frozen(URL_SCHEMES)
        self._doctype_seen = False
        self._recorder = PairRecorder()

    def _record(self, token: Token, surface: str) -> None:
        self._recorder.record(token, surface)

    def _token(self, text: Untrusted[str], surface: str) -> Token:
        token = token_for(self._session, text)
        self._record(token, surface)
        return token

    def pairs(self) -> tuple[tuple[Token, str], ...]:
        return self._recorder.items()

    def _part(
        self,
        part: str,
        surface: str,
        *,
        lower: bool,
        vocab_id: str | None,
        terms: Container[str],
    ) -> EnumValue | Token:
        if vocab_id is not None:
            if part in terms:
                return _term(vocab_id, part)
            if lower and _ascii_lower(part) in terms:
                return _term(vocab_id, _ascii_lower(part))
        return self._token(Untrusted(part), surface)

    def _parts(
        self,
        text: str,
        raw: str,
        *,
        lower: bool,
        vocab_id: str | None = None,
        terms: Container[str] = frozenset(),
    ) -> schema.AttrValue:
        parts = [unwrap(part) for part in normalize.split(Untrusted(text))]
        if len(parts) > _MAX_PARTS:
            return self._token(Untrusted(text), surface_of(raw))
        surfaces = _aligned(raw, parts)
        items = tuple(
            self._part(part, surface, lower=lower, vocab_id=vocab_id, terms=terms)
            for part, surface in zip(parts, surfaces, strict=True)
        )
        return items[0] if len(items) == 1 else items

    def _tag(self, name: str) -> EnumValue | Token:
        if name in self._elements:
            return _term(HTML_ELEMENTS, name)
        return self._token(Untrusted(name), surface_of(name))

    def _attr_name(self, name: str) -> EnumValue | Token:
        if name in self._attributes:
            return _term(HTML_ATTRIBUTES, name)
        return self._token(Untrusted(name), surface_of(name))

    def _url(self, text: Untrusted[str], raw: str) -> schema.Url | Token:
        censored = censor_url(self._session, text, self._schemes, self._record)
        if isinstance(censored, Token):
            self._record(censored, surface_of(raw))
        return censored

    def _attr_value(
        self, element: str, name: str, value: Untrusted[str] | None
    ) -> schema.AttrValue:
        if value is None:
            return True
        text = normalize.value(value)
        plain = unwrap(text)
        if not plain:
            return True
        raw = unwrap(value)
        allowed = self._values.values_for(element, name)
        if allowed is not None:
            return self._parts(plain, raw, lower=True, vocab_id=HTML_VALUES, terms=allowed)
        if name == "class":
            return self._parts(plain, raw, lower=False, vocab_id=CSS_BOOTSTRAP, terms=self._classes)
        if name in _TOKEN_LIST:
            return self._parts(plain, raw, lower=False)
        if name in _URL_ATTRIBUTES:
            return self._url(text, raw)
        return self._token(text, surface_of(raw))

    def _attr(self, element: str, name: str, value: Untrusted[str] | None) -> schema.Attr:
        return schema.Attr(
            name=self._attr_name(name),
            value=self._attr_value(element, name, value),
        )

    def _element(self, node: ingest.Element) -> schema.Element:
        tag = unwrap(node.tag)
        attrs = tuple(self._attr(tag, unwrap(name), value) for name, value in node.attributes)
        if tag in _RAW_TEXT or (
            tag in _TEXT_ONLY and all(isinstance(child, ingest.Text) for child in node.children)
        ):
            children = self._raw_text(node.children)
        else:
            children = self._nodes(node.children)
        return schema.Element(tag=self._tag(tag), attrs=attrs, children=children)

    def _raw_text(self, nodes: tuple[ingest.Node, ...]) -> tuple[schema.Node, ...]:
        """Script and style content is one Token; any element inside fails closed.

        Title and textarea use the same path only when every child is Text (see _element).
        """
        pieces: list[str] = []
        for node in nodes:
            if not isinstance(node, ingest.Text):
                raise AsfbError(ErrorCode.E_HTML_PARSE)
            pieces.append(unwrap(node.text))
        raw = "".join(pieces)
        text = normalize.value(Untrusted(raw))
        if not unwrap(text):
            return ()
        return (schema.Text(text=self._token(text, surface_of(raw))),)

    def document(self, nodes: tuple[ingest.Node, ...], profile: Profile) -> DocV1:
        return DocV1(
            profile=profile,
            source=SourceKind.HTML,
            vocab=_declared(),
            body=self._nodes(nodes),
        )

    def _nodes(self, nodes: tuple[ingest.Node, ...]) -> tuple[schema.Node, ...]:
        result: list[schema.Node] = []
        for node in nodes:
            converted = self._node(node)
            if converted is not None:
                result.append(converted)
        return tuple(result)

    def _node(self, node: object) -> schema.Node | None:
        match node:
            case ingest.Element():
                return self._element(node)
            case ingest.Text():
                text = normalize.value(node.text)
                if not unwrap(text):
                    return None
                return schema.Text(text=self._token(text, surface_of(unwrap(node.text))))
            case ingest.Comment():
                return self._opaque(schema.OpaqueForm.COMMENT, node.text)
            case ingest.CData():
                return self._opaque(schema.OpaqueForm.CDATA, node.text)
            case ingest.Declaration():
                return self._opaque(schema.OpaqueForm.DECL, node.text)
            case ingest.ProcessingInstruction():
                return self._opaque(schema.OpaqueForm.PI, node.text)
            case ingest.Doctype():
                return self._doctype(node)
        raise AsfbError(ErrorCode.E_HTML_PARSE)

    def _opaque(self, form: schema.OpaqueForm, text: Untrusted[str]) -> schema.Opaque:
        return schema.Opaque(form=form, text=self._token(text, surface_of(unwrap(text))))

    def _doctype(self, node: ingest.Doctype) -> schema.Doctype:
        if self._doctype_seen:
            raise AsfbError(ErrorCode.E_HTML_PARSE)
        self._doctype_seen = True
        return schema.Doctype(html=_ascii_lower(unwrap(normalize.value(node.text))) == "html")


def censor_with_pairs(
    key: SessionKey, nodes: tuple[ingest.Node, ...], profile: Profile, /
) -> tuple[DocV1, SurfacePairs]:
    """Censor a raw tree into the v1 document and the (token, surface) pairs it minted."""
    censor = _Censor(key)
    model = censor.document(nodes, profile)
    return model, SurfacePairs(censor.pairs())


def censor_document(key: SessionKey, nodes: tuple[ingest.Node, ...], profile: Profile, /) -> DocV1:
    """Censor a raw tree into the v1 document. Raises AsfbError with a static code."""
    model, _pairs = censor_with_pairs(key, nodes, profile)
    return model
