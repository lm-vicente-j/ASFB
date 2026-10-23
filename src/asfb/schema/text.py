"""The v1 plain text model: paragraphs of lines, each line one token, and the line ending kind."""

from typing import Annotated, Literal

from pydantic import Field

from asfb.schema.doc import Profile, VocabRef
from asfb.types.base import StrictModel
from asfb.types.token import Token

MAX_LINES = 20_000
MAX_INDENT = 64


class TextLine(StrictModel):
    indent: Annotated[int, Field(ge=0, le=MAX_INDENT)]
    text: Token


class TextParagraph(StrictModel):
    lines: Annotated[tuple[TextLine, ...], Field(min_length=1, max_length=MAX_LINES)]


class TextV1(StrictModel):
    schema_id: Literal["asfb.text/1"] = "asfb.text/1"
    profile: Profile
    vocab: Annotated[tuple[VocabRef, ...], Field(max_length=0)]
    eol: Literal["lf", "crlf", "cr", "mixed", "none"]
    paragraphs: Annotated[tuple[TextParagraph, ...], Field(max_length=MAX_LINES)]
