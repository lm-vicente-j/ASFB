"""A compiled checker for the closed JSON Schemas of the emitted documents.

compile_schema turns one schema into a tree of Python closures, once per process. The checker
answers the same question as jsonschema's Draft 2020-12 is_valid for the subset of keywords the
committed schemas use, and nothing else: a keyword outside that subset is refused at compile
time (SchemaError), never ignored. Annotations (title, description, default, discriminator and
the like) are ignored, as jsonschema ignores them. The subset is checked by differential tests
against jsonschema on the committed schemas.

One deliberate difference, always on the strict side: jsonschema (Draft 6 and later) takes an
integer-valued float such as 1.0 as an "integer"; this checker takes only Python ints. It never
accepts what jsonschema rejects.

The checker walks the whole document in one call. It holds no state between calls and caches no
results; the only cache is the compiled tree of each $defs entry.
"""

import re
from collections.abc import Callable, Mapping
from typing import Final, cast

Check = Callable[[object], bool]

_ANNOTATIONS: Final = frozenset(
    {"$schema", "$id", "$defs", "title", "description", "default", "discriminator", "examples"}
)
_KEYWORDS: Final = frozenset(
    {
        "$ref",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "propertyNames",
        "items",
        "enum",
        "const",
        "anyOf",
        "oneOf",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    }
)
_REF_PREFIX: Final = "#/$defs/"


class SchemaError(ValueError):
    """The schema uses a construct this checker does not compile, or is malformed."""


def _accept(_value: object) -> bool:
    return True


def _reject(_value: object) -> bool:
    return False


def _is_object(value: object) -> bool:
    return isinstance(value, dict)


def _is_array(value: object) -> bool:
    return isinstance(value, list)


def _is_string(value: object) -> bool:
    return isinstance(value, str)


def _is_boolean(value: object) -> bool:
    return isinstance(value, bool)


def _is_null(value: object) -> bool:
    return value is None


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


_TYPES: Final[dict[str, Check]] = {
    "object": _is_object,
    "array": _is_array,
    "string": _is_string,
    "boolean": _is_boolean,
    "null": _is_null,
    "integer": _is_integer,
    "number": _is_number,
}


def _all(checks: list[Check]) -> Check:
    if not checks:
        return _accept
    if len(checks) == 1:
        return checks[0]
    frozen = tuple(checks)

    def run(value: object) -> bool:
        for check in frozen:
            if not check(value):
                return False
        return True

    return run


def _any(checks: list[Check]) -> Check:
    if len(checks) == 1:
        return checks[0]
    frozen = tuple(checks)

    def run(value: object) -> bool:
        for check in frozen:
            if check(value):
                return True
        return False

    return run


def _one(checks: list[Check]) -> Check:
    frozen = tuple(checks)

    def run(value: object) -> bool:
        count = 0
        for check in frozen:
            if check(value):
                count += 1
                if count > 1:
                    return False
        return count == 1

    return run


def _scalars(node: object, where: str) -> tuple[object, ...]:
    """The values of an enum or a const: strings, booleans, integers or null. Nothing else."""
    if not isinstance(node, list):
        raise SchemaError(f"{where} needs a list")
    for item in node:
        if item is not None and not isinstance(item, str | bool | int):
            raise SchemaError(f"{where} takes only strings, booleans, integers and null")
    return tuple(node)


def _strings(node: object, where: str) -> tuple[str, ...]:
    values = _scalars(node, where)
    if not all(isinstance(item, str) for item in values):
        raise SchemaError(f"{where} needs a list of strings")
    return cast(tuple[str, ...], values)


def _same(value: object, expected: object) -> bool:
    # JSON equality keeps booleans apart from integers, so the types must match too.
    return type(value) is type(expected) and value == expected


class _Compiler:
    def __init__(self, root: Mapping[str, object]) -> None:
        defs = root.get("$defs", {})
        if not isinstance(defs, dict):
            raise SchemaError("$defs must be an object")
        self._defs: dict[str, object] = dict(defs)
        # name -> compiled check, or None while the definition is still being compiled.
        self._cache: dict[str, Check | None] = {}
        self._holders: dict[str, list[Check]] = {}

    def ref(self, pointer: object) -> Check:
        if not isinstance(pointer, str) or not pointer.startswith(_REF_PREFIX):
            raise SchemaError("only local $defs references are compiled")
        name = pointer[len(_REF_PREFIX) :]
        if name not in self._defs:
            raise SchemaError("unknown $defs reference")
        if name in self._cache:
            done = self._cache[name]
            if done is not None:
                return done
            # A recursive reference: call through the holder filled when the definition ends.
            pending = self._holders[name]

            def call(value: object) -> bool:
                return pending[0](value)

            return call
        holder: list[Check] = []
        self._holders[name] = holder
        self._cache[name] = None
        compiled = self.node(self._defs[name])
        holder.append(compiled)
        self._cache[name] = compiled
        return compiled

    def node(self, schema: object) -> Check:
        if schema is True:
            return _accept
        if schema is False:
            return _reject
        if not isinstance(schema, dict):
            raise SchemaError("a schema must be an object or a boolean")
        unknown = set(schema) - _KEYWORDS - _ANNOTATIONS
        if unknown:
            raise SchemaError("keyword outside the compiled subset")
        checks: list[Check] = []
        if "$ref" in schema:
            checks.append(self.ref(schema["$ref"]))
        if "type" in schema:
            checks.append(self._type(schema["type"]))
        if "enum" in schema:
            checks.append(self._enum(schema["enum"], "enum"))
        if "const" in schema:
            checks.append(self._enum([schema["const"]], "const"))
        if "pattern" in schema:
            checks.append(self._pattern(schema["pattern"]))
        if "minLength" in schema:
            checks.append(self._length("minLength", schema["minLength"], _is_string))
        if "maxLength" in schema:
            checks.append(self._length("maxLength", schema["maxLength"], _is_string))
        if "minItems" in schema:
            checks.append(self._length("minItems", schema["minItems"], _is_array))
        if "maxItems" in schema:
            checks.append(self._length("maxItems", schema["maxItems"], _is_array))
        if "minimum" in schema:
            checks.append(self._bound("minimum", schema["minimum"]))
        if "maximum" in schema:
            checks.append(self._bound("maximum", schema["maximum"]))
        if "items" in schema:
            checks.append(self._items(schema["items"]))
        if "properties" in schema or "required" in schema or "additionalProperties" in schema:
            checks.append(self._object(schema))
        if "propertyNames" in schema:
            checks.append(self._property_names(schema["propertyNames"]))
        if "anyOf" in schema:
            checks.append(_any(self._branches(schema["anyOf"], "anyOf")))
        if "oneOf" in schema:
            checks.append(_one(self._branches(schema["oneOf"], "oneOf")))
        return _all(checks)

    def _branches(self, node: object, where: str) -> list[Check]:
        if not isinstance(node, list) or not node:
            raise SchemaError(f"{where} needs a non-empty list")
        return [self.node(branch) for branch in node]

    def _type(self, node: object) -> Check:
        names = [node] if isinstance(node, str) else _strings(node, "type")
        tests: list[Check] = []
        for name in names:
            if name not in _TYPES:
                raise SchemaError("unknown type")
            tests.append(_TYPES[name])
        return _any(tests)

    def _enum(self, node: object, where: str) -> Check:
        values = _scalars(node, where)
        strings = frozenset(item for item in values if isinstance(item, str))
        others = tuple(item for item in values if not isinstance(item, str))

        def run(value: object) -> bool:
            if type(value) is str:
                return value in strings
            for expected in others:
                if _same(value, expected):
                    return True
            return False

        return run

    def _pattern(self, node: object) -> Check:
        if not isinstance(node, str):
            raise SchemaError("pattern needs a string")
        try:
            compiled = re.compile(node)
        except re.error:
            raise SchemaError("pattern does not compile") from None

        def run(value: object) -> bool:
            return not isinstance(value, str) or compiled.search(value) is not None

        return run

    def _length(self, keyword: str, node: object, kind: Check) -> Check:
        if isinstance(node, bool) or not isinstance(node, int) or node < 0:
            raise SchemaError("a length bound needs a non-negative integer")
        bound = node
        lower = keyword.startswith("min")

        def run(value: object) -> bool:
            if not kind(value):
                return True
            size = len(cast(str | list[object], value))
            return size >= bound if lower else size <= bound

        return run

    def _bound(self, keyword: str, node: object) -> Check:
        if not _is_number(node):
            raise SchemaError("a numeric bound needs a number")
        limit = cast(int | float, node)
        lower = keyword == "minimum"

        def run(value: object) -> bool:
            if not _is_number(value):
                return True
            number = cast(int | float, value)
            return number >= limit if lower else number <= limit

        return run

    def _items(self, node: object) -> Check:
        if isinstance(node, list):
            raise SchemaError("tuple items are not compiled")
        each = self.node(node)

        def run(value: object) -> bool:
            if not isinstance(value, list):
                return True
            for item in value:
                if not each(item):
                    return False
            return True

        return run

    def _object(self, schema: dict[str, object]) -> Check:
        raw_properties = schema.get("properties", {})
        if not isinstance(raw_properties, dict):
            raise SchemaError("properties must be an object")
        properties = tuple((name, self.node(sub)) for name, sub in raw_properties.items())
        required = _strings(schema.get("required", []), "required")
        extra_node = schema.get("additionalProperties", True)
        known = frozenset(raw_properties)
        # Modes: no extras check, no extras allowed (a set comparison), or each extra checked.
        forbid_extra = extra_node is False
        extra: Check | None = None
        if extra_node is not True and not forbid_extra:
            extra = self.node(extra_node)

        def run(value: object) -> bool:
            if not isinstance(value, dict):
                return True
            for name in required:
                if name not in value:
                    return False
            for name, check in properties:
                if name in value and not check(value[name]):
                    return False
            if forbid_extra:
                return value.keys() <= known
            if extra is not None:
                for name, item in value.items():
                    if name not in known and not extra(item):
                        return False
            return True

        return run

    def _property_names(self, node: object) -> Check:
        each = self.node(node)

        def run(value: object) -> bool:
            if not isinstance(value, dict):
                return True
            for name in value:
                if not each(name):
                    return False
            return True

        return run


def compile_schema(schema: Mapping[str, object]) -> Check:
    """The checker of one schema: a function from a parsed JSON value to True when valid."""
    if not isinstance(schema, Mapping):
        raise SchemaError("the root schema must be an object")
    return _Compiler(schema).node(dict(schema))
