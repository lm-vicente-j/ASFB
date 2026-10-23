"""Python censoring: the lexemes of the stdlib tokenizer, each one public or one token.

Public: keywords, soft keywords, builtins, stdlib and exception names that equal a term of the
py.* vocabularies exactly; the operators; the numbers 0 and 1; newline, indent and dedent (with
the depth). Everything else is a token whose surface is the raw text: a name, another number, a
string (docstrings included), a comment body (the `#` is shown by the view) and a whole f-string,
pieces and interpolated expressions together. The file name is a token and its extension is
public. Line numbers are the start line of each lexeme.

The lexemes come from ingest/code.py as offsets; this module is the only one that slices the
text, and only to build surfaces. Pairs go only to the vault. Inner helpers raise AsfbError only
through the caller's public function.

Speed, with the output unchanged: a lexeme is a row until the whole list is validated in one batch
(schema.LEXEME_BATCH), and a surface is minted once per document (the token is a pure function of
the key and the raw text, and the pair recorder keeps first-seen order). _MEMOIZE switches the
memo off; the tests check that both settings give the same model and pairs.
"""

import bisect
import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Final, Literal, cast

from asfb.errors import AsfbError, ErrorCode
from asfb.ingest import limits
from asfb.ingest.code import CodeSource, RawToken
from asfb.sanitize.pairs import PairRecorder, SurfacePairs, surface_of
from asfb.sanitize.tokens import token_for
from asfb.schema import code as schema
from asfb.schema.doc import Profile, VocabRef
from asfb.types.key import SessionKey
from asfb.types.token import Token
from asfb.types.untrusted import Untrusted, unwrap
from asfb.types.values import EnumValue
from asfb.vocab import FrozenVocabulary, py_registry

_FSTRING_START: Final = frozenset({"FSTRING_START", "TSTRING_START"})
_FSTRING_END: Final = frozenset({"FSTRING_END", "TSTRING_END"})
_WORD_KINDS: Final = (
    ("keyword", "py.keywords"),
    ("exception", "py.exceptions"),
    ("builtin", "py.builtins"),
    ("stdlib", "py.stdlib"),
)
_EXTENSIONS: Final[dict[str, Literal["py", "pyi"]]] = {".py": "py", ".pyi": "pyi"}
_MEMOIZE: bool = True
Row = dict[str, object]


@dataclass(frozen=True, slots=True, eq=False)
class CodeCensus:
    """A censored source: the model, the minted pairs and the span of each lexeme (offsets)."""

    model: schema.CodeV1
    pairs: SurfacePairs
    spans: tuple[tuple[int, int], ...]


class _Minter:
    """Mints the token of a raw surface and records its pair. With memo, each raw text is minted
    once per document; a repeat is the same token and the same pair, so the output is unchanged."""

    __slots__ = ("_memo", "_recorder", "_token_of")

    def __init__(self, key: SessionKey, recorder: PairRecorder, memoize: bool) -> None:
        # The key is bound once into the callable; no attribute of this class holds it.
        self._token_of: Callable[[Untrusted[str]], Token] = partial(token_for, key)
        self._recorder = recorder
        self._memo: dict[str, Token] | None = {} if memoize else None

    def __call__(self, raw: str) -> Token:
        if self._memo is not None:
            known = self._memo.get(raw)
            if known is not None:
                return known
        token = self._token_of(Untrusted(raw))
        self._recorder.record(token, surface_of(raw))
        if self._memo is not None:
            self._memo[raw] = token
        return token


def _vocab(vocab_id: str) -> FrozenVocabulary:
    entry = py_registry().get(vocab_id)
    if not isinstance(entry, FrozenVocabulary):
        raise AsfbError(ErrorCode.E_VOCAB_DATA)
    return entry


def _line(starts: list[int], offset: int) -> int:
    return bisect.bisect_right(starts, offset)


def _line_starts(text: str) -> list[int]:
    starts = [0]
    starts.extend(index + 1 for index, char in enumerate(text) if char == "\n")
    return starts


def _prefix(raw: str) -> schema.Prefix:
    """The prefix class of a string: fstring (f or t), bytes (b), raw (r) or str."""
    quote = min(index for index in (raw.find('"'), raw.find("'")) if index >= 0)
    letters = raw[:quote].lower()
    if "f" in letters or "t" in letters:
        return "fstring"
    if "b" in letters:
        return "bytes"
    if "r" in letters:
        return "raw"
    return "str"


def _row(line: int, kind: str, value: object, prefix: object, depth: object) -> Row:
    """One lexeme as the fields of schema.Lexeme; the batch validation checks them."""
    return {"line": line, "kind": kind, "value": value, "prefix": prefix, "depth": depth}


def _check_bytes(raw: str) -> None:
    if len(raw.encode("utf-8")) > limits.MAX_CODE_TOKEN_BYTES:
        raise AsfbError(ErrorCode.E_CODE_TOO_LARGE)


def _fstring_end(tokens: tuple[RawToken, ...], start: int) -> int:
    """The index of the end token that closes the f-string starting at index start."""
    depth = 0
    for index in range(start, len(tokens)):
        kind = tokens[index].kind
        if kind in _FSTRING_START:
            depth += 1
        elif kind in _FSTRING_END:
            depth -= 1
            if depth == 0:
                return index
    raise AsfbError(ErrorCode.E_CODE_TOKENIZE_FAILED)


def _word(word: str, vocabs: dict[str, FrozenVocabulary]) -> tuple[str, EnumValue | None]:
    for kind, vocab_id in _WORD_KINDS:
        if word in vocabs[vocab_id]:
            return kind, EnumValue(vocab=vocab_id, term=word)
    return "name", None


def _lexeme(
    mint: _Minter,
    text: str,
    starts: list[int],
    token: RawToken,
    vocabs: dict[str, FrozenVocabulary],
    depth: int,
) -> tuple[Row, int]:
    """One lexeme of a single raw token as a row, and the indentation depth after it."""
    raw = text[token.start : token.end]
    line = _line(starts, token.start)
    kind = token.kind
    if kind == "NEWLINE":
        return _row(line, "newline", None, None, None), depth
    if kind == "INDENT":
        depth += 1
        return _row(line, "indent", None, None, depth), depth
    if kind == "DEDENT":
        depth -= 1
        return _row(line, "dedent", None, None, depth), depth
    if kind == "NAME":
        word_kind, term = _word(raw, vocabs)
        if term is not None:
            return _row(line, word_kind, term, None, None), depth
        return _row(line, "name", mint(raw), None, None), depth
    if kind == "NUMBER":
        if raw in schema.NUMBERS:
            value: object = cast(schema.NumberText, raw)
        else:
            value = mint(raw)
        return _row(line, "number", value, None, None), depth
    if kind == "OP":
        if raw not in schema.OPS:
            raise AsfbError(ErrorCode.E_CODE_TOKENIZE_FAILED)
        return _row(line, "op", cast(schema.OpText, raw), None, None), depth
    if kind == "COMMENT":
        return _row(line, "comment", mint(raw[1:]), None, None), depth
    if kind == "STRING":
        string = mint(raw)
        return _row(line, "string", string, _prefix(raw), None), depth
    raise AsfbError(ErrorCode.E_CODE_TOKENIZE_FAILED)


def _file(mint: _Minter, name: str | None) -> tuple[Token | None, Literal["py", "pyi"] | None]:
    """The file token (its stem) and the public extension, or None for both."""
    if name is None:
        return None, None
    if "/" in name or "\\" in name:
        raise AsfbError(ErrorCode.E_SOURCE_UNSUPPORTED)
    stem, suffix = os.path.splitext(name)
    ext = _EXTENSIONS.get(suffix.lower())
    if ext is None:
        raise AsfbError(ErrorCode.E_SOURCE_UNSUPPORTED)
    return mint(stem), ext


def censor_code(
    key: SessionKey, source: CodeSource, profile: Profile, name: str | None, /
) -> CodeCensus:
    """Censor a lexed Python source into the v1 code model, its pairs and its lexeme spans."""
    text = unwrap(source.text)
    starts = _line_starts(text)
    vocabs = {vocab_id: _vocab(vocab_id) for vocab_id in schema.PY_VOCAB_IDS}
    recorder = PairRecorder()
    mint = _Minter(key, recorder, _MEMOIZE)
    file_token, ext = _file(mint, name)
    rows: list[Row] = []
    spans: list[tuple[int, int]] = []
    tokens = source.tokens
    depth = 0
    index = 0
    while index < len(tokens):
        if len(rows) == limits.MAX_CODE_LEXEMES:
            # ingest counts the same lexemes and refuses first; this stops the list before the
            # next lexeme is minted or validated, so the bound is local to the model too.
            raise AsfbError(ErrorCode.E_CODE_TOO_MANY_LEXEMES)
        token = tokens[index]
        if token.kind in _FSTRING_START:
            last = _fstring_end(tokens, index)
            end = tokens[last].end
            raw = text[token.start : end]
            _check_bytes(raw)
            rows.append(_row(_line(starts, token.start), "string", mint(raw), "fstring", None))
            spans.append((token.start, end))
            index = last + 1
            continue
        row, depth = _lexeme(mint, text, starts, token, vocabs, depth)
        rows.append(row)
        spans.append((token.start, token.end))
        index += 1
    lexemes = schema.LEXEME_BATCH.validate_python(rows)
    vocab = tuple(VocabRef(id=entry.id, version=entry.version) for entry in vocabs.values())
    model = schema.CodeV1(
        profile=profile,
        vocab=vocab,
        file=file_token,
        ext=ext,
        dropped=source.dropped,
        lexemes=lexemes,
    )
    return CodeCensus(model, SurfacePairs(recorder.items()), tuple(spans))
