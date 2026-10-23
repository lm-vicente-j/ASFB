"""tools/gen_py_vocab.py reproduces the committed py.* vocabularies byte for byte under the pinned
CPython 3.12 patch. The interpreter is ASFB_PY312 when that is set; otherwise the uv default for
CPython 3.12 (~/.local/share/uv/python, or %APPDATA%/uv/python on Windows). The test is skipped,
with its reason, when no patch is pinned or no interpreter of that exact patch is found."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from asfb.errors import ErrorCode
from asfb.vocab import FrozenVocabulary, _build

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tools" / "gen_py_vocab.py"


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_py_vocab", GENERATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pinned_interpreter(patch: int) -> str | None:
    """The CPython 3.12 interpreter of the pinned patch, or None. Checked by its own version."""
    named = os.environ.get("ASFB_PY312")
    candidates: list[Path] = [Path(named)] if named else []
    if not named:
        roots = [Path.home() / ".local" / "share" / "uv" / "python"]
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "uv" / "python")
        for root in roots:
            candidates.extend(sorted(root.glob("cpython-3.12.*/bin/python3.12")))
            candidates.extend(sorted(root.glob("cpython-3.12.*/python.exe")))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        probe = subprocess.run(  # noqa: S603
            [str(candidate), "-I", "-c", "import sys; print(*sys.version_info[:3])"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.split() == ["3", "12", str(patch)]:
            return str(candidate)
    return None


def test_generator_refuses_to_run_on_another_interpreter() -> None:
    generator = _generator()
    if sys.version_info[:3] == (3, 12, generator.PINNED_PATCH):
        pytest.skip(
            "the running interpreter is the pinned CPython 3.12 patch: the generator accepts it"
        )
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0


def test_generator_reproduces_the_committed_vocabularies() -> None:
    generator = _generator()
    if generator.PINNED_PATCH is None:
        pytest.skip("no CPython 3.12 patch is pinned in tools/gen_py_vocab.py (PINNED_PATCH)")
    interpreter = _pinned_interpreter(generator.PINNED_PATCH)
    if interpreter is None:
        pytest.skip(
            f"no CPython 3.12.{generator.PINNED_PATCH} found: set ASFB_PY312 or install it with uv"
        )
    result = subprocess.run(  # noqa: S603
        [interpreter, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_loader_checks_the_sha256_when_present() -> None:
    terms = ["alpha", "beta"]
    document = {"id": "test.digest", "version": 1, "terms": terms}
    document["sha256"] = _generator().digest(terms)
    assert isinstance(_build("test.digest", json.dumps(document)), FrozenVocabulary)
    document["sha256"] = "0" * 64
    assert _build("test.digest", json.dumps(document)) is ErrorCode.E_VOCAB_DATA
