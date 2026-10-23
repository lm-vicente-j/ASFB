"""The v1 Python source model: a flat list of lexemes, each with a line and a closed kind.

Public kinds carry a public value: a vocabulary term (keyword, builtin, stdlib, exception), one of
the 48 operators of the tokenizer, or the numbers 0 and 1. Every other kind carries a token. A
string carries its prefix class; indent and dedent carry the depth. Newline has no value.
"""

from typing import Annotated, Final, Literal, Self, get_args

from pydantic import Field, TypeAdapter, model_validator

from asfb.schema.doc import Profile, VocabRef
from asfb.types.base import StrictModel
from asfb.types.token import Token
from asfb.types.values import EnumValue

MAX_LEXEMES = 50_000  # the ingest lexeme limit (ingest/limits.py MAX_CODE_LEXEMES)
MAX_LINE = 20_000  # the ingest line limit (ingest/limits.py MAX_CODE_LINES)
MAX_DEPTH = 100  # the ingest indentation limit
MAX_DROPPED = 1_048_576
Kind = Literal[
    "keyword",
    "builtin",
    "stdlib",
    "exception",
    "op",
    "name",
    "number",
    "string",
    "comment",
    "newline",
    "indent",
    "dedent",
]
Prefix = Literal["str", "bytes", "fstring", "raw"]
Ext = Literal["py", "pyi"]
NumberText = Literal["0", "1"]
OpText = Literal[
    "!",
    "!=",
    "%",
    "%=",
    "&",
    "&=",
    "(",
    ")",
    "*",
    "**",
    "**=",
    "*=",
    "+",
    "+=",
    ",",
    "-",
    "-=",
    "->",
    ".",
    "...",
    "/",
    "//",
    "//=",
    "/=",
    ":",
    ":=",
    ";",
    "<",
    "<<",
    "<<=",
    "<=",
    "=",
    "==",
    ">",
    ">=",
    ">>",
    ">>=",
    "@",
    "@=",
    "[",
    "]",
    "^",
    "^=",
    "{",
    "|",
    "|=",
    "}",
    "~",
]
OPS: Final = frozenset(get_args(OpText))
NUMBERS: Final = frozenset(get_args(NumberText))
TERM_KINDS: Final = {
    "keyword": "py.keywords",
    "builtin": "py.builtins",
    "stdlib": "py.stdlib",
    "exception": "py.exceptions",
}
PY_VOCAB_IDS: Final = ("py.builtins", "py.exceptions", "py.keywords", "py.stdlib")


class Lexeme(StrictModel):
    line: Annotated[int, Field(ge=1, le=MAX_LINE)]
    kind: Kind
    value: EnumValue | Token | OpText | NumberText | None
    prefix: Prefix | None
    depth: Annotated[int, Field(ge=0, le=MAX_DEPTH)] | None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.kind in TERM_KINDS:
            expected = TERM_KINDS[self.kind]
            if not isinstance(self.value, EnumValue) or self.value.vocab != expected:
                raise ValueError("term kind needs its vocabulary")
            if self.prefix is not None or self.depth is not None:
                raise ValueError("term kind takes no prefix or depth")
        elif self.kind == "op":
            if not isinstance(self.value, str) or self.value not in OPS:
                raise ValueError("op kind needs an operator")
            if self.prefix is not None or self.depth is not None:
                raise ValueError("op kind takes no prefix or depth")
        elif self.kind == "number":
            literal = isinstance(self.value, str) and self.value in NUMBERS
            if not (literal or isinstance(self.value, Token)):
                raise ValueError("number kind needs 0, 1 or a token")
            if self.prefix is not None or self.depth is not None:
                raise ValueError("number kind takes no prefix or depth")
        elif self.kind in ("name", "comment"):
            if not isinstance(self.value, Token) or self.prefix is not None:
                raise ValueError("name and comment kinds need a token and no prefix")
            if self.depth is not None:
                raise ValueError("name and comment kinds take no depth")
        elif self.kind == "string":
            if not isinstance(self.value, Token) or self.prefix is None:
                raise ValueError("string kind needs a token and a prefix class")
            if self.depth is not None:
                raise ValueError("string kind takes no depth")
        elif self.kind == "newline":
            if self.value is not None or self.prefix is not None or self.depth is not None:
                raise ValueError("newline kind takes nothing")
        else:
            if self.value is not None or self.prefix is not None or self.depth is None:
                raise ValueError("indent and dedent take only a depth")
        return self


# The lexeme list as one batch: compiled once per process, used by sanitize/code.py.
LEXEME_BATCH: Final = TypeAdapter(tuple[Lexeme, ...])


class CodeV1(StrictModel):
    schema_id: Literal["asfb.code/1"] = "asfb.code/1"
    profile: Profile
    language: Literal["python"] = "python"
    vocab: Annotated[tuple[VocabRef, ...], Field(max_length=len(PY_VOCAB_IDS))]
    file: Token | None
    ext: Ext | None
    dropped: Annotated[int, Field(ge=0, le=MAX_DROPPED)]
    lexemes: Annotated[tuple[Lexeme, ...], Field(max_length=MAX_LEXEMES)]

    @model_validator(mode="after")
    def _file_pair(self) -> Self:
        if (self.file is None) != (self.ext is None):
            raise ValueError("file and ext come together")
        if tuple(ref.id for ref in self.vocab) != PY_VOCAB_IDS:
            raise ValueError("code declares exactly the four python vocabularies")
        return self
