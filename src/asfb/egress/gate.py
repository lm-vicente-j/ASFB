"""Single exit for documents: validate, check vocabulary and strings, then emit JSON."""

import json
from collections.abc import Mapping
from importlib import resources
from typing import Any, Final, NewType, NoReturn

from jsonschema import Draft202012Validator
from pydantic import BaseModel

from asfb.errors import EgressError, ErrorCode
from asfb.schema import SCHEMA_RESOURCE
from asfb.schema.doc import DocV1
from asfb.types.token import SHAPE_RE
from asfb.types.values import EnumValue
from asfb.vocab import VocabRegistry, Vocabulary

Emitted = NewType("Emitted", str)


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


_SCHEMA_TEXT = resources.files("asfb.schema").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")
_SCHEMA: Final[dict[str, Any]] = json.loads(_SCHEMA_TEXT)
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR: Final = Draft202012Validator(_SCHEMA)
_KEYS, _WORDS = _schema_names(_SCHEMA)


def _declare(model: DocV1, vocab: VocabRegistry) -> dict[str, Vocabulary] | None:
    declared: dict[str, Vocabulary] = {}
    for ref in model.vocab:
        entry = vocab.get(ref.id)
        if (
            ref.id in declared
            or entry is None
            or entry.id != ref.id
            or entry.version != ref.version
        ):
            return None
        declared[ref.id] = entry
    pending: list[object] = [model.body]
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


def _word_ok(node: str, declared: Mapping[str, Vocabulary]) -> bool:
    return (
        SHAPE_RE.fullmatch(node) is not None
        or node in _WORDS
        or node in declared
        or any(node in vocab for vocab in declared.values())
    )


def _strings_ok(node: object, declared: Mapping[str, Vocabulary]) -> bool:
    pending: list[object] = [node]
    while pending:
        current = pending.pop()
        if current is None or isinstance(current, bool) or type(current) is int:
            continue
        if isinstance(current, dict):
            if not all(isinstance(key, str) and key in _KEYS for key in current):
                return False
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, str):
            if not _word_ok(current, declared):
                return False
        else:
            return False
    return True


def _dump(model: DocV1) -> str:
    # model_dump_json stops near 48 elements (pydantic-core depth guard); the dict form does not.
    plain = model.model_dump(mode="json", warnings="error")
    return json.dumps(plain, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _reserializes(model: DocV1, raw: str) -> bool:
    return _dump(model) == raw


def _check(doc: object, vocab: VocabRegistry) -> ErrorCode | str:
    stage = ErrorCode.E_EGRESS_TYPE
    try:
        if type(doc) is not DocV1:
            return stage
        stage = ErrorCode.E_EGRESS_SERIALIZE
        raw = _dump(doc)
        stage = ErrorCode.E_EGRESS_SCHEMA
        model = DocV1.model_validate_json(raw, strict=True)
        stage = ErrorCode.E_EGRESS_JSONSCHEMA
        instance = _parse_strict(raw)
        if not _VALIDATOR.is_valid(instance):
            return stage
        stage = ErrorCode.E_EGRESS_VOCAB
        declared = _declare(model, vocab)
        if declared is None:
            return stage
        stage = ErrorCode.E_EGRESS_STRING
        if not _strings_ok(instance, declared):
            return stage
        stage = ErrorCode.E_EGRESS_ROUNDTRIP
        if not _reserializes(model, raw):
            return stage
        return raw
    except Exception:  # noqa: BLE001
        return stage


def emit(doc: DocV1, /, *, vocab: VocabRegistry) -> Emitted:
    outcome = _check(doc, vocab)
    if isinstance(outcome, ErrorCode):
        raise EgressError(outcome)
    return Emitted(outcome)
