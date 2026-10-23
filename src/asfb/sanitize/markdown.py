"""Markdown censoring: raw blocks become schema blocks, and their text is scanned for inlines here.

Inline scanning is one pass per line. Code spans, images, links and autolinks are recognised; any
other character joins a text buffer. Buffers are normalized and tokenized; a buffer that normalizes
to nothing is dropped. A line with more than 1024 inlines becomes one text token. Entities and
escapes are not decoded: the surface is the raw text. Embedded HTML, front matter and code are one
token each. Pairs go only to the vault.
"""

import re
import string
from typing import Final

from asfb import normalize
from asfb.errors import AsfbError, ErrorCode
from asfb.ingest.markdown import (
    RawBlock,
    RawCode,
    RawFrontMatter,
    RawHeading,
    RawHtml,
    RawItem,
    RawLinkDef,
    RawParagraph,
    RawRule,
    RawTable,
)
from asfb.sanitize.pairs import PairRecorder, SurfacePairs, surface_of
from asfb.sanitize.tokens import token_for
from asfb.sanitize.urls import censor_url
from asfb.schema import md as schema
from asfb.schema.doc import Profile, VocabRef
from asfb.schema.html import Url
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted, unwrap
from asfb.types.values import EnumValue
from asfb.vocab import MD_IDS, FrozenVocabulary, md_registry

MD_LANGUAGES: Final = "md.languages"
URL_SCHEMES: Final = "url.schemes"
_LOWER: Final = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_PUNCTUATION: Final = frozenset(string.punctuation)
_RUN: Final = re.compile(r"`+")
_TARGET: Final = (
    r"\(\s*(<[^>\n]*>|[^\s()]{0,2048})(?:\s+(\"[^\"\n]{0,1000}\"|'[^'\n]{0,1000}'))?\s*\)"
)
_IMAGE: Final = re.compile(r"!\[([^\]\n]{0,1000})\]" + _TARGET)
_LINK: Final = re.compile(r"\[([^\]\n]{0,1000})\]" + _TARGET)
_AUTOLINK: Final = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]{1,2048})>")

Inline = schema.MdText | schema.MdCode | schema.MdLink | schema.MdImage | schema.MdAutolink
Block = (
    schema.Heading
    | schema.Paragraph
    | schema.Item
    | schema.CodeBlock
    | schema.Table
    | schema.Rule
    | schema.HtmlBlock
    | schema.FrontMatter
    | schema.LinkDef
)


def _frozen(vocab_id: str) -> FrozenVocabulary:
    entry = md_registry()[vocab_id]
    if not isinstance(entry, FrozenVocabulary):
        raise AsfbError(ErrorCode.E_VOCAB_DATA)
    return entry


def _declared() -> tuple[VocabRef, ...]:
    registry = md_registry()
    return tuple(
        VocabRef(id=registry[vocab_id].id, version=registry[vocab_id].version)
        for vocab_id in sorted(MD_IDS)
    )


def _run_end(text: str, start: int) -> int:
    end = start
    while end < len(text) and text[end] == "`":
        end += 1
    return end


def _closing_run(text: str, start: int, size: int) -> int | None:
    """Where the next backtick run of exactly `size` starts, if there is one."""
    for found in _RUN.finditer(text, start):
        if len(found.group()) == size:
            return found.start()
    return None


def _code_content(content: str) -> str:
    """A code span loses one space at each end when both ends have one and it is not all spaces."""
    if len(content) >= 2 and content[0] == " " and content[-1] == " " and content.strip(" "):
        return content[1:-1]
    return content


def _destination(raw: str) -> str:
    return raw[1:-1] if raw.startswith("<") else raw


class _Censor:
    """Walks the raw blocks. Holds the session key, the vocabularies and the pairs, nothing else."""

    def __init__(self, key: SessionKey) -> None:
        self._session = key
        self._languages = _frozen(MD_LANGUAGES)
        self._schemes = _frozen(URL_SCHEMES)
        self._recorder = PairRecorder()

    def pairs(self) -> tuple[tuple[Token, str], ...]:
        return self._recorder.items()

    def _token(self, value: Untrusted[str], raw: str) -> Token:
        token = token_for(self._session, value)
        self._recorder.record(token, surface_of(raw))
        return token

    def _text(self, raw: str) -> Token | None:
        """The token of a text that is not blank after normalization, else None."""
        value = normalize.value(Untrusted(raw))
        if not unwrap(value):
            return None
        return self._token(value, raw)

    def _always(self, raw: str) -> Token:
        return self._token(normalize.value(Untrusted(raw)), raw)

    def _url(self, raw: str) -> Url | Token | None:
        value = normalize.value(Untrusted(_destination(raw)))
        if not unwrap(value):
            return None
        censored = censor_url(self._session, value, self._schemes, self._recorder.record)
        if isinstance(censored, Token):
            self._recorder.record(censored, surface_of(_destination(raw)))
        return censored

    def _title(self, raw: str | None) -> Token | None:
        return None if raw is None else self._text(raw[1:-1])

    def _lang(self, info: Untrusted[str] | None) -> EnumValue | Token | None:
        if info is None:
            return None
        raw = unwrap(info)
        value = normalize.value(Untrusted(raw))
        word = unwrap(value)
        if not word:
            return None
        lowered = word.translate(_LOWER)
        if lowered in self._languages:
            return EnumValue(vocab=MD_LANGUAGES, term=lowered)
        return self._token(value, raw)

    def inlines(self, raw: str) -> tuple[Inline, ...]:
        """The inlines of one line. More than MAX_INLINES of them make the whole line one text."""
        items = self._scan(raw)
        if len(items) > schema.MAX_INLINES:
            whole = self._text(raw)
            return () if whole is None else (schema.MdText(text=whole),)
        return tuple(items)

    def _scan(self, raw: str) -> list[Inline]:
        items: list[Inline] = []
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                text = "".join(buffer)
                buffer.clear()
                token = self._text(text)
                if token is not None:
                    items.append(schema.MdText(text=token))

        index = 0
        while index < len(raw):
            char = raw[index]
            if char == "`":
                end = _run_end(raw, index)
                close = _closing_run(raw, end, end - index)
                if close is not None:
                    token = self._text(_code_content(raw[end:close]))
                    if token is not None:
                        flush()
                        items.append(schema.MdCode(text=token))
                        index = close + (end - index)
                        continue
                buffer.append(raw[index:end])
                index = end
                continue
            found = self._link_like(raw, index)
            if found is not None:
                node, stop = found
                flush()
                items.append(node)
                index = stop
                continue
            if char == "\\" and index + 1 < len(raw) and raw[index + 1] in _PUNCTUATION:
                buffer.append(raw[index : index + 2])
                index += 2
                continue
            buffer.append(char)
            index += 1
        flush()
        return items

    def _link_like(self, raw: str, index: int) -> tuple[Inline, int] | None:
        char = raw[index]
        if char in "![":
            image = _IMAGE.match(raw, index) if char == "!" else None
            if image is not None:
                alt = self._text(image.group(1))
                url = self._url(image.group(2))
                picture = schema.MdImage(alt=alt, url=url, title=self._title(image.group(3)))
                return picture, image.end()
            link = _LINK.match(raw, index) if char == "[" else None
            if link is not None:
                text = self._text(link.group(1))
                url = self._url(link.group(2))
                anchor = schema.MdLink(text=text, url=url, title=self._title(link.group(3)))
                return anchor, link.end()
        if char == "<":
            auto = _AUTOLINK.match(raw, index)
            if auto is not None:
                url = self._url(auto.group(1))
                if url is not None:
                    return schema.MdAutolink(url=url), auto.end()
        return None

    def block(self, raw: RawBlock) -> Block:
        if isinstance(raw, RawHeading):
            return schema.Heading(
                quote=raw.quote,
                level=raw.level,
                inlines=self.inlines(unwrap(raw.text)),
            )
        if isinstance(raw, RawParagraph):
            if len(raw.lines) > schema.MAX_LINES:
                raise AsfbError(ErrorCode.E_MD_BLOCKS)
            return schema.Paragraph(
                quote=raw.quote,
                lines=tuple(self.inlines(unwrap(line)) for line in raw.lines),
            )
        if isinstance(raw, RawItem):
            return schema.Item(
                quote=raw.quote,
                depth=raw.depth,
                ordered=raw.ordered,
                task=raw.task,
                inlines=self.inlines(unwrap(raw.text)),
            )
        if isinstance(raw, RawCode):
            return schema.CodeBlock(
                quote=raw.quote,
                fence=raw.fence,
                lang=self._lang(raw.info),
                text=self._text(unwrap(raw.text)),
            )
        if isinstance(raw, RawTable):
            return self._table(raw)
        if isinstance(raw, RawRule):
            return schema.Rule(quote=raw.quote)
        if isinstance(raw, RawHtml):
            return schema.HtmlBlock(quote=raw.quote, text=self._always(unwrap(raw.text)))
        if isinstance(raw, RawFrontMatter):
            return schema.FrontMatter(text=self._text(unwrap(raw.text)))
        if isinstance(raw, RawLinkDef):
            return schema.LinkDef(
                quote=raw.quote,
                label=self._always(unwrap(raw.label)),
                url=None if raw.dest is None else self._url(unwrap(raw.dest)),
                # The ingest already removed the quotes of a definition title.
                title=None if raw.title is None else self._text(unwrap(raw.title)),
            )
        raise AsfbError(ErrorCode.E_MD_BLOCKS)

    def _table(self, raw: RawTable) -> schema.Table:
        if len(raw.rows) > schema.MAX_ROWS:
            raise AsfbError(ErrorCode.E_MD_BLOCKS)
        return schema.Table(
            quote=raw.quote,
            align=raw.align,
            header=tuple(self.inlines(unwrap(cell)) for cell in raw.header),
            rows=tuple(tuple(self.inlines(unwrap(cell)) for cell in row) for row in raw.rows),
        )


def censor_md_with_pairs(
    key: SessionKey, blocks: tuple[RawBlock, ...], profile: Profile, /
) -> tuple[schema.MdV1, SurfacePairs]:
    """Censor raw markdown blocks into the v1 model and the (token, surface) pairs it minted."""
    censor = _Censor(key)
    model = schema.MdV1(
        profile=profile,
        vocab=_declared(),
        blocks=tuple(censor.block(block) for block in blocks),
    )
    return model, SurfacePairs(censor.pairs())
