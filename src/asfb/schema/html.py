"""HTML body shapes: elements, text, opaque constructs, doctype and URLs."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field

from asfb.types.base import StrictModel
from asfb.types.token import Token
from asfb.types.values import EnumValue


class Url(StrictModel):
    scheme: EnumValue | None = None
    authority: Token | None = None
    rooted: bool = False
    path: tuple[Token, ...] = Field((), max_length=256)
    trailing_slash: bool = False
    query: Token | None = None
    fragment: Token | None = None


def _exact_true(value: Any) -> Any:
    # Literal[True] alone also accepts the JSON integer 1; only the boolean true passes.
    if value is not True:
        raise ValueError("expected true")
    return value


AttrValue = (
    Annotated[Literal[True], BeforeValidator(_exact_true)]
    | Token
    | EnumValue
    | Url
    | Annotated[tuple[EnumValue | Token, ...], Field(max_length=128)]
)


class Attr(StrictModel):
    name: EnumValue | Token
    value: AttrValue


class Element(StrictModel):
    kind: Literal["element"] = "element"
    tag: EnumValue | Token
    attrs: tuple[Attr, ...] = Field((), max_length=64)
    children: tuple["Node", ...] = Field((), max_length=20000)


class Text(StrictModel):
    kind: Literal["text"] = "text"
    text: Token


class OpaqueForm(StrEnum):
    COMMENT = "comment"
    CDATA = "cdata"
    PI = "pi"
    DECL = "decl"


class Opaque(StrictModel):
    kind: Literal["opaque"] = "opaque"
    form: OpaqueForm
    text: Token


class Doctype(StrictModel):
    kind: Literal["doctype"] = "doctype"
    html: bool


Node = Annotated[Element | Text | Opaque | Doctype, Field(discriminator="kind")]

Element.model_rebuild()
