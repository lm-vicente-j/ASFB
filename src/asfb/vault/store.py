"""In-memory vault: the originals behind tokens, and the censored documents with their revisions.

One Vault per Session, nothing on disk. Surfaces and resolved paths are plain str held here
(unwrap is allowed in vault/). Surfaces live in three scopes: the whole session (accumulated),
each document (its latest revision only) and path segments (directory names and stems).
Every class that holds originals has a redacted repr and raises on pickling and copying; none of
them ever appears in a Doc, an output or an error.

Document ids are HMAC(session key, "asfb.docid/1\\0" + normcased absolute path), first 16 hex
digits: stable per file and key, and they reveal no path. Caps raise E_VAULT_FULL.
"""

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, NoReturn, SupportsIndex, final

from asfb.egress import CensoredView, Emitted
from asfb.errors import AsfbError, ErrorCode
from asfb.sanitize.html import SurfacePairs
from asfb.sanitize.tokens import token_for
from asfb.schema.doc import Profile
from asfb.types.docid import DocId
from asfb.types.key import SessionKey
from asfb.types.token import SHAPE_RE as TOKEN_RE
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted

MAX_TOKENS: Final = 200_000
MAX_SURFACES: Final = 64
MAX_DOCUMENTS: Final = 1_024
MAX_REVISIONS: Final = 16
_DOC_DOMAIN: Final = b"asfb.docid/1\x00"
_DOC_RE: Final = re.compile(r"d_[0-9a-f]{16}")


class _Sealed:
    """Base for classes that hold originals: redacted, never pickled or copied."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> NoReturn:
        raise TypeError(f"{type(self).__name__} cannot be pickled")

    def __copy__(self) -> NoReturn:
        raise TypeError(f"{type(self).__name__} cannot be copied")

    def __deepcopy__(self, memo: Any = None, /) -> NoReturn:
        raise TypeError(f"{type(self).__name__} cannot be copied")


def _group(pairs: Iterable[tuple[Token, str]]) -> dict[Token, set[str]]:
    grouped: dict[Token, set[str]] = {}
    for token, surface in pairs:
        grouped.setdefault(token, set()).add(surface)
    return grouped


def _merge(table: dict[Token, set[str]], additions: Mapping[Token, set[str]]) -> None:
    for token, surfaces in additions.items():
        table.setdefault(token, set()).update(surfaces)


def doc_id(key: SessionKey, path: str) -> DocId:
    """The id of a document: HMAC over the normcased absolute path, 16 hex digits."""
    name = os.path.normcase(os.path.abspath(path)).encode("utf-8", "surrogatepass")
    return DocId("d_" + key.digest(_DOC_DOMAIN + name)[:8].hex())


@dataclass(frozen=True, slots=True)
class DocRef:
    """A document and one of its revisions. Prints as d_<id>@<revision>."""

    doc: DocId
    revision: int

    def __str__(self) -> str:
        return f"{self.doc}@{self.revision}"


@dataclass(frozen=True, slots=True)
class _Revision:
    number: int
    digest: bytes
    json: Emitted
    view: CensoredView


@final
class _Record(_Sealed):
    """One document: its resolved path (memory only) and its revisions, oldest first."""

    __slots__ = ("path", "profile", "revisions")

    def __init__(self, path: str, profile: Profile) -> None:
        self.path = path
        self.profile = profile
        self.revisions: list[_Revision] = []


@final
class Stored(_Sealed):
    """A stored revision with the path it came from. Internal to the session; never output."""

    __slots__ = ("digest", "json", "path", "profile", "ref", "view")

    def __init__(
        self,
        ref: DocRef,
        path: str,
        profile: Profile,
        digest: bytes,
        json: Emitted,
        view: CensoredView,
    ) -> None:
        self.ref = ref
        self.path = path
        self.profile = profile
        self.digest = digest
        self.json = json
        self.view = view


@final
class Vault(_Sealed):
    """Tokens with their surfaces, and documents with their revisions. Holds the session key."""

    __slots__ = (
        "_doc_tokens",
        "_docs",
        "_model_tokens",
        "_path_tokens",
        "_session_key",
        "_tokens",
    )

    def __init__(self, key: SessionKey, /) -> None:
        self._session_key = key
        self._model_tokens: set[Token] = set()
        self._tokens: dict[Token, set[str]] = {}
        self._path_tokens: dict[Token, set[str]] = {}
        self._doc_tokens: dict[DocId, dict[Token, set[str]]] = {}
        self._docs: dict[DocId, _Record] = {}

    def known(self, token: Token) -> bool:
        return token in self._tokens

    def mark_model(self, tokens: Iterable[Token], /) -> None:
        """Mark tokens that the agent introduced through an edit (origin=model). Only known ones."""
        self._model_tokens.update(token for token in tokens if token in self._tokens)

    def model_origin(self, token: Token, /) -> bool:
        return token in self._model_tokens

    def surfaces(self, token: Token) -> frozenset[str]:
        """Every surface of the token in the session scope."""
        return frozenset(self._tokens.get(token, set()))

    def surfaces_for(self, token: Token, /, *, doc: DocId | None = None) -> frozenset[str]:
        """The surfaces in one scope: the document's latest revision if the token is there,
        otherwise the whole session. Empty when the token is unknown."""
        if doc is not None:
            scoped = self._doc_tokens.get(doc, {}).get(token)
            if scoped is not None:
                return frozenset(scoped)
        return self.surfaces(token)

    def path_surfaces(self, token: Token, /) -> frozenset[str]:
        """The surfaces minted for path segments only: directory names and file stems."""
        return frozenset(self._path_tokens.get(token, set()))

    def _check(self, additions: Mapping[Token, set[str]]) -> None:
        fresh = 0
        for token, surfaces in additions.items():
            current = self._tokens.get(token)
            if current is None:
                fresh += 1
            elif len(current | surfaces) > MAX_SURFACES:
                raise AsfbError(ErrorCode.E_VAULT_FULL)
            if len(surfaces) > MAX_SURFACES:
                raise AsfbError(ErrorCode.E_VAULT_FULL)
        if len(self._tokens) + fresh > MAX_TOKENS:
            raise AsfbError(ErrorCode.E_VAULT_FULL)

    def _apply(self, additions: Mapping[Token, set[str]]) -> None:
        self._check(additions)
        _merge(self._tokens, additions)

    def record(self, pairs: SurfacePairs, /, *, doc: DocId | None = None) -> None:
        """Add the (token, surface) pairs of a censoring run. Pairs accumulate in the session.
        With doc, that document's table is replaced: it holds the latest revision only."""
        additions = _group(pairs.items())
        if doc is not None and doc not in self._doc_tokens:
            if len(self._doc_tokens) >= MAX_DOCUMENTS:
                raise AsfbError(ErrorCode.E_VAULT_FULL)
        self._apply(additions)
        if doc is not None:
            self._doc_tokens[doc] = additions

    def mint_path(self, segments: Sequence[str], /) -> tuple[Token, ...]:
        """Tokens for path segments (directory names and the file stem), recorded as surfaces."""
        tokens = tuple(token_for(self._session_key, Untrusted(segment)) for segment in segments)
        additions = _group(zip(tokens, segments, strict=True))
        self._apply(additions)
        _merge(self._path_tokens, additions)
        return tokens

    def translate_arg(
        self,
        text: str,
        /,
        *,
        unknown: ErrorCode = ErrorCode.E_PATH_UNKNOWN_TOKEN,
        doc: DocId | None = None,
    ) -> str:
        """The single surface of one token in a scope. Unknown or non-token text gives `unknown`;
        a token with several surfaces gives E_TOKEN_AMBIGUOUS."""
        if TOKEN_RE.fullmatch(text) is None:
            raise AsfbError(unknown)
        found = self.surfaces_for(Token(str(text)), doc=doc)
        if not found:
            raise AsfbError(unknown)
        if len(found) > 1:
            raise AsfbError(ErrorCode.E_TOKEN_AMBIGUOUS)
        (surface,) = found
        return surface

    def translate_text(
        self,
        text: str,
        /,
        *,
        unknown: ErrorCode = ErrorCode.E_PATH_UNKNOWN_TOKEN,
        doc: DocId | None = None,
    ) -> str:
        """Replace every token in text by its single surface. Any unknown token fails the call."""
        return TOKEN_RE.sub(
            lambda match: self.translate_arg(match.group(0), unknown=unknown, doc=doc), text
        )

    def doc_id(self, path: str, /) -> DocId:
        return doc_id(self._session_key, path)

    def find(self, doc: DocId, /) -> Stored | None:
        """The latest revision of a document, or None when it was never stored."""
        if _DOC_RE.fullmatch(doc) is None:
            return None
        record = self._docs.get(doc)
        if record is None or not record.revisions:
            return None
        return self._stored(doc, record, record.revisions[-1])

    def get(self, doc: DocId, revision: int | None = None, /) -> Stored:
        """One revision (the latest when None). Unknown ids or revisions give E_DOC_UNKNOWN."""
        record = self._docs.get(doc)
        if record is None or not record.revisions:
            raise AsfbError(ErrorCode.E_DOC_UNKNOWN)
        if revision is None:
            return self._stored(doc, record, record.revisions[-1])
        for item in record.revisions:
            if item.number == revision:
                return self._stored(doc, record, item)
        raise AsfbError(ErrorCode.E_DOC_UNKNOWN)

    def _stored(self, doc: DocId, record: _Record, item: _Revision) -> Stored:
        return Stored(
            DocRef(doc, item.number),
            record.path,
            record.profile,
            item.digest,
            item.json,
            item.view,
        )

    def put_document(
        self,
        path: str,
        profile: Profile,
        digest: bytes,
        emitted: Emitted,
        view: CensoredView,
        /,
    ) -> DocRef:
        """Store a censored document. The same digest as the latest revision returns that one."""
        doc = doc_id(self._session_key, path)
        record = self._docs.get(doc)
        if record is None:
            if len(self._docs) >= MAX_DOCUMENTS:
                raise AsfbError(ErrorCode.E_VAULT_FULL)
            record = _Record(path, profile)
            self._docs[doc] = record
        latest = record.revisions[-1] if record.revisions else None
        if latest is not None and latest.digest == digest:
            return DocRef(doc, latest.number)
        if len(record.revisions) >= MAX_REVISIONS:
            raise AsfbError(ErrorCode.E_VAULT_FULL)
        number = 1 if latest is None else latest.number + 1
        record.revisions.append(_Revision(number, digest, emitted, view))
        return DocRef(doc, number)
