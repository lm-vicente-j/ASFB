"""Single exit for documents: validate, check vocabulary and strings, then emit JSON.

The allowed set is closed: the models in _MODELS, each with its own committed schema file. Property
names and const/enum strings come from that file; the DocId pattern is an allowed string form
for ListV1 only. Add every new emitted model to _MODELS and to Emittable.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final, NewType, NoReturn

from jsonschema import Draft202012Validator
from pydantic import BaseModel

from asfb.egress.schema_check import Check, compile_schema
from asfb.errors import EgressError, ErrorCode
from asfb.schema import (
    CODE_RESOURCE,
    DB_RESOURCE,
    DIFF_RESOURCE,
    LIST_RESOURCE,
    LOG_RESOURCE,
    MD_RESOURCE,
    RUN_RESOURCE,
    SCHEMA_RESOURCE,
    TEXT_RESOURCE,
)
from asfb.schema.code import CodeV1
from asfb.schema.db import DbV1
from asfb.schema.diff import DiffV1
from asfb.schema.doc import DocV1
from asfb.schema.list import ListV1
from asfb.schema.log import LogV1
from asfb.schema.md import MdV1
from asfb.schema.run import RunV1
from asfb.schema.text import TextV1
from asfb.types.docid import SHAPE_RE as DOC_ID_RE
from asfb.types.token import SHAPE_RE
from asfb.types.values import EnumValue
from asfb.vocab import VocabRegistry, Vocabulary

Emitted = NewType("Emitted", str)
Emittable = DocV1 | ListV1 | DiffV1 | RunV1 | TextV1 | MdV1 | CodeV1 | LogV1 | DbV1
# Per document, the string walk checks each distinct word once. Tests switch it off and compare.
_MEMOIZE: bool = True


@dataclass(frozen=True, slots=True, eq=False)
class _Spec:
    """What one emitted schema allows: its model, schema, checker, names, words and shapes."""

    model: type[BaseModel]
    schema: dict[str, Any]
    validator: Check
    keys: frozenset[str]
    words: frozenset[str]
    shapes: tuple[re.Pattern[str], ...]


def _schema_names(schema: object) -> tuple[frozenset[str], frozenset[str]]:
    keys: set[str] = set()
    words: set[str] = set()
    pending: list[object] = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    keys.update(name for name in value if isinstance(name, str))
                elif key == "const" and isinstance(value, str):
                    words.add(value)
                elif key == "enum" and isinstance(value, list):
                    words.update(item for item in value if isinstance(item, str))
                pending.append(value)
        elif isinstance(node, list):
            pending.extend(node)
    return frozenset(keys), frozenset(words)


def _reject_constant(name: str) -> NoReturn:
    raise ValueError("non-finite number")


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse_strict(raw: str) -> object:
    parsed: object = json.loads(
        raw, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant
    )
    return parsed


def _spec(model: type[BaseModel], resource: str, shapes: tuple[re.Pattern[str], ...]) -> _Spec:
    # Once per process: the metaschema check through jsonschema, then the compiled checker.
    text = resources.files("asfb.schema").joinpath(resource).read_text(encoding="utf-8")
    schema: dict[str, Any] = json.loads(text)
    Draft202012Validator.check_schema(schema)
    keys, words = _schema_names(schema)
    return _Spec(model, schema, compile_schema(schema), keys, words, shapes)


_MODELS: Final[tuple[tuple[type[BaseModel], str, tuple[re.Pattern[str], ...]], ...]] = (
    (DocV1, SCHEMA_RESOURCE, (SHAPE_RE,)),
    (ListV1, LIST_RESOURCE, (SHAPE_RE, DOC_ID_RE)),
    (DiffV1, DIFF_RESOURCE, (SHAPE_RE, DOC_ID_RE)),
    (RunV1, RUN_RESOURCE, (SHAPE_RE,)),
    (TextV1, TEXT_RESOURCE, (SHAPE_RE,)),
    (MdV1, MD_RESOURCE, (SHAPE_RE,)),
    (CodeV1, CODE_RESOURCE, (SHAPE_RE,)),
    (LogV1, LOG_RESOURCE, (SHAPE_RE,)),
    (DbV1, DB_RESOURCE, (SHAPE_RE,)),
)
_SPECS: Final[dict[type, _Spec]] = {
    model: _spec(model, resource, shapes) for model, resource, shapes in _MODELS
}


def _declare(model: BaseModel, vocab: VocabRegistry) -> dict[str, Vocabulary] | None:
    body_vocab = getattr(model, "vocab", ())
    declared: dict[str, Vocabulary] = {}
    for ref in body_vocab:
        entry = vocab.get(ref.id)
        if (
            ref.id in declared
            or entry is None
            or entry.id != ref.id
            or entry.version != ref.version
        ):
            return None
        declared[ref.id] = entry
    pending: list[object] = list(vars(model).values())
    while pending:
        node = pending.pop()
        if isinstance(node, EnumValue):
            if node.vocab not in declared or node.term not in declared[node.vocab]:
                return None
        elif isinstance(node, BaseModel):
            pending.extend(vars(node).values())
        elif isinstance(node, tuple):
            pending.extend(node)
    return declared


def _word_ok(node: str, declared: Mapping[str, Vocabulary], spec: _Spec) -> bool:
    return (
        any(shape.fullmatch(node) is not None for shape in spec.shapes)
        or node in spec.words
        or node in declared
        or any(node in vocab for vocab in declared.values())
    )


def _word_verdict(
    word: str, declared: Mapping[str, Vocabulary], spec: _Spec, memo: dict[str, bool] | None
) -> bool:
    """_word_ok, once per distinct word of one document when memo is given: a pure function of
    the word, with declared and spec fixed for the whole document."""
    if memo is None:
        return _word_ok(word, declared, spec)
    known = memo.get(word)
    if known is None:
        known = memo[word] = _word_ok(word, declared, spec)
    return known


def _strings_ok(
    node: object,
    declared: Mapping[str, Vocabulary],
    spec: _Spec,
    memo: dict[str, bool] | None = None,
) -> bool:
    pending: list[object] = [node]
    while pending:
        current = pending.pop()
        if current is None or isinstance(current, bool) or type(current) is int:
            continue
        if isinstance(current, dict):
            if not all(isinstance(key, str) and key in spec.keys for key in current):
                return False
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, str):
            if not _word_verdict(current, declared, spec, memo):
                return False
        else:
            return False
    return True


def _dump(model: BaseModel) -> str:
    # model_dump_json stops near 48 elements (pydantic-core depth guard); the dict form does not.
    plain = model.model_dump(mode="json", warnings="error")
    return json.dumps(plain, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _reserializes(model: BaseModel, raw: str) -> bool:
    return _dump(model) == raw


def _check(doc: object, vocab: VocabRegistry) -> ErrorCode | str:
    stage = ErrorCode.E_EGRESS_TYPE
    try:
        spec = _SPECS.get(type(doc))
        if spec is None or not isinstance(doc, BaseModel):
            return stage
        stage = ErrorCode.E_EGRESS_SERIALIZE
        raw = _dump(doc)
        stage = ErrorCode.E_EGRESS_SCHEMA
        model = spec.model.model_validate_json(raw, strict=True)
        stage = ErrorCode.E_EGRESS_JSONSCHEMA
        instance = _parse_strict(raw)
        if not spec.validator(instance):
            return stage
        stage = ErrorCode.E_EGRESS_VOCAB
        declared = _declare(model, vocab)
        if declared is None:
            return stage
        stage = ErrorCode.E_EGRESS_STRING
        memo: dict[str, bool] | None = {} if _MEMOIZE else None
        if not _strings_ok(instance, declared, spec, memo):
            return stage
        stage = ErrorCode.E_EGRESS_ROUNDTRIP
        if not _reserializes(model, raw):
            return stage
        return raw
    except Exception:  # noqa: BLE001
        return stage


def emit(doc: Emittable, /, *, vocab: VocabRegistry) -> Emitted:
    outcome = _check(doc, vocab)
    if isinstance(outcome, ErrorCode):
        raise EgressError(outcome)
    return Emitted(outcome)


def _structural(doc: CodeV1) -> ErrorCode | str:
    stage = ErrorCode.E_EGRESS_SERIALIZE
    try:
        raw = _dump(doc)
        stage = ErrorCode.E_EGRESS_SCHEMA
        CodeV1.model_validate_json(raw, strict=True)
        return raw
    except Exception:  # noqa: BLE001
        return stage


def emit_before_render(doc: Emittable, /, *, vocab: VocabRegistry) -> Emitted:
    """The first exit of a document, for a caller that renders the result at once.

    render() runs the full gate again on the text, so no caller gets an emitted text without it.
    For asfb.code/1 only, this first exit runs the structural check (serialization and the typed
    model) and skips the schema, vocabulary, string and reserialization walks, which render()
    repeats on the same text. Every other model takes the full gate here.
    """
    if not isinstance(doc, CodeV1):
        return emit(doc, vocab=vocab)
    outcome = _structural(doc)
    if isinstance(outcome, ErrorCode):
        raise EgressError(outcome)
    return Emitted(outcome)
