"""The v1 markdown model: blocks in document order, each with its inline pieces.

Text, code, URLs, alt text, titles, labels, HTML and front matter all leave as tokens. The language
of a code fence is a public term of md.languages when it is one, a token otherwise.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from asfb.schema.doc import Profile, VocabRef
from asfb.schema.html import Url
from asfb.types.base import StrictModel
from asfb.types.token import Token
from asfb.types.values import EnumValue

MAX_BLOCKS = 20_000
MAX_INLINES = 1024
MAX_QUOTE = 16
MAX_LIST_DEPTH = 16
MAX_COLUMNS = 64
MAX_ROWS = 10_000
MAX_LINES = 10_000
Quote = Annotated[int, Field(ge=0, le=MAX_QUOTE)]
Align = Literal["none", "left", "center", "right"]


class MdText(StrictModel):
    kind: Literal["text"] = "text"
    text: Token


class MdCode(StrictModel):
    kind: Literal["code"] = "code"
    text: Token


class MdLink(StrictModel):
    kind: Literal["link"] = "link"
    text: Token | None
    url: Url | Token | None
    title: Token | None


class MdImage(StrictModel):
    kind: Literal["image"] = "image"
    alt: Token | None
    url: Url | Token | None
    title: Token | None


class MdAutolink(StrictModel):
    kind: Literal["autolink"] = "autolink"
    url: Url | Token


Inline = Annotated[MdText | MdCode | MdLink | MdImage | MdAutolink, Field(discriminator="kind")]
Inlines = Annotated[tuple[Inline, ...], Field(max_length=MAX_INLINES)]


class Heading(StrictModel):
    kind: Literal["heading"] = "heading"
    quote: Quote
    level: Annotated[int, Field(ge=1, le=6)]
    inlines: Inlines


class Paragraph(StrictModel):
    kind: Literal["paragraph"] = "paragraph"
    quote: Quote
    lines: Annotated[tuple[Inlines, ...], Field(min_length=1, max_length=MAX_LINES)]


class Item(StrictModel):
    kind: Literal["item"] = "item"
    quote: Quote
    depth: Annotated[int, Field(ge=0, le=MAX_LIST_DEPTH)]
    ordered: bool
    task: Literal["none", "open", "done"]
    inlines: Inlines


class CodeBlock(StrictModel):
    kind: Literal["codeblock"] = "codeblock"
    quote: Quote
    fence: Literal["backtick", "tilde", "indent"]
    lang: EnumValue | Token | None
    text: Token | None


class Table(StrictModel):
    kind: Literal["table"] = "table"
    quote: Quote
    align: Annotated[tuple[Align, ...], Field(min_length=1, max_length=MAX_COLUMNS)]
    header: Annotated[tuple[Inlines, ...], Field(max_length=MAX_COLUMNS)]
    rows: Annotated[
        tuple[Annotated[tuple[Inlines, ...], Field(max_length=MAX_COLUMNS)], ...],
        Field(max_length=MAX_ROWS),
    ]

    @model_validator(mode="after")
    def _rectangular(self) -> Self:
        if len(self.header) != len(self.align):
            raise ValueError("header width differs from the alignment row")
        if any(len(row) != len(self.align) for row in self.rows):
            raise ValueError("row width differs from the alignment row")
        return self


class Rule(StrictModel):
    kind: Literal["rule"] = "rule"
    quote: Quote


class HtmlBlock(StrictModel):
    kind: Literal["html"] = "html"
    quote: Quote
    text: Token


class FrontMatter(StrictModel):
    kind: Literal["frontmatter"] = "frontmatter"
    text: Token | None


class LinkDef(StrictModel):
    kind: Literal["linkdef"] = "linkdef"
    quote: Quote
    label: Token
    url: Url | Token | None
    title: Token | None


Block = Annotated[
    Heading | Paragraph | Item | CodeBlock | Table | Rule | HtmlBlock | FrontMatter | LinkDef,
    Field(discriminator="kind"),
]


class MdV1(StrictModel):
    schema_id: Literal["asfb.md/1"] = "asfb.md/1"
    profile: Profile
    vocab: tuple[VocabRef, ...]
    blocks: Annotated[tuple[Block, ...], Field(max_length=MAX_BLOCKS)]
