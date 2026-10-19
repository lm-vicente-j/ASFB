import ast
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

import asfb

SANITIZE = "asfb/sanitize/"
NORMALIZE = "asfb/normalize/"
EGRESS = "asfb/egress/"
INGEST = "asfb/ingest/"
UNTRUSTED = "asfb/types/untrusted.py"
UNTRUSTED_MODULE = "asfb.types.untrusted"
KEY = "asfb/types/key.py"
EMITTED = "Emitted"
CENSORED_VIEW = "CensoredView"
EGRESS_ONLY_NAMES = frozenset({EMITTED, CENSORED_VIEW})
FORBIDDEN_LOOKUP = "unwrap"
UNWRAP_ALLOWED = (SANITIZE, NORMALIZE)
INGEST_FORBIDDEN = (
    "asfb.schema",
    "asfb.egress",
    "asfb.sanitize",
    "asfb.types.token",
    "asfb.types.values",
)
NORMALIZE_FORBIDDEN = ("asfb.schema", "asfb.egress", "asfb.sanitize", "asfb.ingest")
TOKENS = "asfb/sanitize/tokens.py"
TOKENIZE = "tokenize"
VIEW = "asfb/egress/view.py"
VIEW_FORBIDDEN = (
    "asfb.ingest",
    "asfb.normalize",
    "asfb.sanitize",
    "asfb.types.untrusted",
)
ATTRIBUTE_LOOKUPS = frozenset({"getattr", "attrgetter"})
DYNAMIC_IMPORTS = frozenset({"import_module", "__import__"})


def _reference(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.alias):
        return node.name
    return None


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_internal(text: str | None) -> bool:
    return text is not None and (text == "_value" or text.startswith("_Untrusted"))


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _imports_gc(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.partition(".")[0] == "gc" for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.level == 0 and (node.module or "").partition(".")[0] == "gc"
    return False


def _lookup_violations(node: ast.Call) -> Iterator[str]:
    callee = _callee(node.func)
    if callee not in ATTRIBUTE_LOOKUPS:
        return
    if callee == "getattr" and len(node.args) not in (2, 3):
        yield "getattr with an unexpected argument count"
    if any(isinstance(arg, ast.Starred) for arg in node.args):
        yield f"{callee} with unpacked arguments"
    names = node.args[1:2] if callee == "getattr" else node.args
    for arg in names:
        text = _literal(arg)
        if text is None:
            yield f"{callee} with a non-constant attribute name"
        elif text == FORBIDDEN_LOOKUP:
            yield f"{callee} looks up unwrap"


def _annotation_nodes(tree: ast.AST) -> frozenset[int]:
    roots: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                roots.append(node.returns)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
    return frozenset(id(child) for root in roots for child in ast.walk(root))


def _import_targets(path: str, node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level == 0:
        base = node.module or ""
    else:
        package = path.removesuffix(".py").split("/")[:-1]
        kept = package[: len(package) - (node.level - 1)]
        base = ".".join([*kept, node.module] if node.module else kept)
    return [base, *(f"{base}.{alias.name}" for alias in node.names)]


def _is_under(module: str, names: tuple[str, ...]) -> bool:
    return any(module == name or module.startswith(f"{name}.") for name in names)


def _violations(path: str, node: ast.AST, annotated: frozenset[int]) -> Iterator[str]:
    name = _reference(node)
    text = _literal(node)
    attribute = node.attr if isinstance(node, ast.Attribute) else None
    if path != UNTRUSTED and (name == "unwrap" or text == "unwrap"):
        if not path.startswith(UNWRAP_ALLOWED):
            yield "unwrap referenced outside asfb/sanitize and asfb/normalize"
    if path.startswith(INGEST) and any(
        _is_under(target, INGEST_FORBIDDEN) for target in _import_targets(path, node)
    ):
        yield "ingest imports a forbidden module"
    if path.startswith(NORMALIZE) and any(
        _is_under(target, NORMALIZE_FORBIDDEN) for target in _import_targets(path, node)
    ):
        yield "normalize imports a forbidden module"
    if path != TOKENS and (name == TOKENIZE or text == TOKENIZE):
        yield "tokenize referenced outside asfb/sanitize/tokens.py"
    if path == VIEW and any(
        _is_under(target, VIEW_FORBIDDEN) for target in _import_targets(path, node)
    ):
        yield "egress/view.py imports a forbidden module"
    if path != UNTRUSTED and (_is_internal(attribute) or _is_internal(text)):
        yield "untrusted internals used outside untrusted.py"
    if path != KEY and (attribute == "_key" or text == "_key"):
        yield "session key internals used outside key.py"
    if name == "model_construct" or text == "model_construct":
        yield "model_construct used in src"
    if isinstance(node, ast.ImportFrom) and node.module == UNTRUSTED_MODULE:
        if path != UNTRUSTED and any(alias.name == "*" for alias in node.names):
            yield "star import of untrusted module"
    if name in DYNAMIC_IMPORTS or text in DYNAMIC_IMPORTS:
        yield "dynamic import"
    if _imports_gc(node):
        yield "gc imported"
    if isinstance(node, ast.Call):
        yield from _lookup_violations(node)
    if isinstance(node, (ast.Name, ast.Attribute)) and name in EGRESS_ONLY_NAMES:
        if not path.startswith(EGRESS) and id(node) not in annotated:
            yield f"{name} used outside asfb/egress"


def scan(sources: Mapping[str, str]) -> list[str]:
    found: list[str] = []
    for path, source in sources.items():
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            found.append(f"{path}: syntax error")
            continue
        annotated = _annotation_nodes(tree)
        for node in ast.walk(tree):
            found.extend(f"{path}: {message}" for message in _violations(path, node, annotated))
    return found


def _load_src() -> dict[str, str]:
    root = Path(asfb.__file__).parent
    return {
        path.relative_to(root.parent).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
    }


def test_src_respects_architecture() -> None:
    sources = _load_src()
    assert UNTRUSTED in sources
    assert scan(sources) == []


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("asfb/egress/gate.py", "from asfb.types.untrusted import unwrap"),
        ("asfb/types/token.py", "x = unwrap(value)"),
        ("asfb/types/__init__.py", "__all__ = ['unwrap']"),
        ("asfb/types/__init__.py", "from asfb.types.untrusted import *"),
        ("asfb/types/key.py", "x = holder._value"),
        ("asfb/types/key.py", "x = getattr(holder, '_Untrusted__x')"),
        ("asfb/types/key.py", "x = getattr(holder, '_value')"),
        ("asfb/egress/gate.py", "x = Model.model_construct(items=())"),
        ("asfb/types/token.py", "x = Model.model_construct"),
        ("asfb/types/token.py", "x = Emitted('abc')"),
        ("asfb/vocab/__init__.py", "x = egress.Emitted('abc')"),
        ("asfb/types/token.py", "x = getattr(holder, name)"),
        ("asfb/types/token.py", "x = getattr(holder, 'unwrap')"),
        ("asfb/sanitize/tokens.py", "x = getattr(holder, 'unwrap')"),
        ("asfb/types/token.py", "x = getattr(holder)"),
        ("asfb/types/token.py", "x = getattr(*pair)"),
        ("asfb/types/token.py", "f = operator.attrgetter(name)"),
        ("asfb/types/token.py", "f = attrgetter('unwrap')"),
        ("asfb/types/token.py", "f = attrgetter('value', 'unwrap')"),
        ("asfb/types/token.py", "f = attrgetter(*names)"),
        ("asfb/types/token.py", "x = getattr(holder, '_key')"),
        ("asfb/types/token.py", "x = holder._key"),
        ("asfb/types/untrusted.py", "x = holder._key"),
        ("asfb/types/token.py", "m = importlib.import_module('json')"),
        ("asfb/types/token.py", "m = import_module('json')"),
        ("asfb/types/token.py", "m = __import__('json')"),
        ("asfb/types/token.py", "import gc"),
        ("asfb/types/token.py", "import gc.abc"),
        ("asfb/types/token.py", "from gc import collect"),
        ("asfb/types/token.py", "E = Emitted"),
        ("asfb/types/token.py", "x = cast(Emitted, value)"),
        ("asfb/types/token.py", "def f(x):\n    return Emitted(x)"),
        ("asfb/vocab/__init__.py", "from asfb.egress import Emitted\nE = Emitted"),
        ("asfb/ingest/html.py", "x = unwrap(v)"),
        ("asfb/ingest/html.py", "from asfb.types.untrusted import unwrap"),
        ("asfb/ingest/html.py", "from asfb.schema import doc"),
        ("asfb/ingest/source.py", "import asfb.sanitize.tokens"),
        ("asfb/ingest/html.py", "from asfb.types.values import EnumValue"),
        ("asfb/ingest/html.py", "from asfb.types.token import Token"),
        ("asfb/ingest/html.py", "from asfb.types import token"),
        ("asfb/ingest/html.py", "from asfb import egress"),
        ("asfb/ingest/source.py", "from ..egress import emit"),
        ("asfb/ingest/html.py", "from .. import sanitize"),
        ("asfb/api.py", "x = tokenize(key, value)"),
        ("asfb/sanitize/html.py", "from asfb.sanitize.tokens import tokenize"),
        ("asfb/normalize/__init__.py", "x = tokenize(key, value)"),
        ("asfb/api.py", "view = CensoredView('abc')"),
        ("asfb/sanitize/html.py", "x = CensoredView(text)"),
        ("asfb/api.py", "from asfb.egress.view import CensoredView\nv = CensoredView('a')"),
        ("asfb/egress/view.py", "from asfb.normalize import value"),
        ("asfb/egress/view.py", "from asfb.sanitize.html import censor_document"),
        ("asfb/egress/view.py", "from asfb.types.untrusted import Untrusted"),
        ("asfb/egress/view.py", "from asfb import ingest"),
        ("asfb/egress/view.py", "from ..ingest.html import parse_html"),
        ("asfb/egress/view.py", "import asfb.normalize"),
        ("asfb/api.py", "from typing import cast\nx = cast(CensoredView, s)"),
        ("asfb/api.py", "V = CensoredView"),
        ("asfb/sanitize/html.py", "from asfb.egress.view import CensoredView\nV = CensoredView"),
        ("asfb/normalize/__init__.py", "from asfb.schema import doc"),
        ("asfb/normalize/__init__.py", "from asfb import ingest"),
        ("asfb/normalize/__init__.py", "import asfb.sanitize.tokens"),
        ("asfb/normalize/__init__.py", "from ..egress import emit"),
        ("asfb/normalize/__init__.py", "from asfb.ingest.html import parse_html"),
    ],
)
def test_scanner_flags_violation(path: str, source: str) -> None:
    assert scan({path: source}) != []


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("asfb/sanitize/tokens.py", "from asfb.types.untrusted import unwrap\nx = unwrap(v)"),
        ("asfb/normalize/__init__.py", "from asfb.types.untrusted import unwrap\nx = unwrap(v)"),
        (
            "asfb/ingest/html.py",
            "from asfb.types.untrusted import Untrusted\nfrom asfb.errors import ErrorCode",
        ),
        ("asfb/ingest/source.py", "from asfb.ingest.html import parse_html"),
        ("asfb/ingest/html.py", "from asfb.types import untrusted"),
        ("asfb/ingest/source.py", "from . import html\nfrom .limits import MAX_BYTES"),
        ("asfb/types/untrusted.py", "def unwrap(v):\n    return v._value"),
        ("asfb/egress/gate.py", "x = Emitted('abc')"),
        ("asfb/egress/gate.py", "E = Emitted"),
        ("asfb/types/key.py", "x = self._key"),
        ("asfb/types/token.py", "x = 'value'"),
        ("asfb/sanitize/tokens.py", "x = getattr(holder, 'value')"),
        ("asfb/types/token.py", "x = getattr(holder, 'value', None)"),
        ("asfb/types/token.py", "f = attrgetter('value')"),
        ("asfb/types/token.py", "import importlib"),
        ("asfb/types/token.py", "from asfb.egress import Emitted"),
        ("asfb/sanitize/tokens.py", "x = tokenize(key, value)"),
        ("asfb/sanitize/tokens.py", "def tokenize(key, value):\n    return value"),
        ("asfb/egress/view.py", "x = CensoredView('a')"),
        ("asfb/egress/view.py", "from asfb.egress.gate import Emitted, emit"),
        ("asfb/egress/view.py", "from asfb.types.token import Token"),
        ("asfb/egress/view.py", "from asfb.vocab import html_registry"),
        (
            "asfb/api.py",
            "from asfb.egress import CensoredView\n"
            "def f(v: CensoredView) -> CensoredView:\n    return v",
        ),
        ("asfb/vocab/__init__.py", "x: Emitted | None = None"),
        ("asfb/api.py", "from asfb.egress import CensoredView\nx: CensoredView | None = None"),
        ("asfb/normalize/__init__.py", "import unicodedata\nx = unicodedata.normalize('NFC', s)"),
        ("asfb/normalize/__init__.py", "from asfb.errors import ErrorCode"),
        (
            "asfb/vocab/__init__.py",
            "from asfb.egress import Emitted\ndef f(x: Emitted) -> Emitted:\n    return x",
        ),
    ],
)
def test_scanner_allows_defining_and_permitted_sites(path: str, source: str) -> None:
    assert scan({path: source}) == []


def test_scanner_reports_syntax_errors() -> None:
    assert scan({"asfb/types/x.py": "def ("}) != []
