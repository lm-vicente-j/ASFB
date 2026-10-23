"""The package init is lazy: the worker's import chain stays free of jsonschema and egress."""

import json
import subprocess
import sys

import pytest

import asfb

PROBE = (
    "import json, sys; import asfb.sandbox.worker; "
    "print(json.dumps(sorted(m for m in sys.modules if m.startswith("
    "('jsonschema', 'asfb.egress', 'asfb.api', 'asfb.session', 'asfb.vault')))))"
)


def _loaded_by(code: str) -> list[str]:
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr[-500:]
    loaded: list[str] = json.loads(done.stdout.strip().splitlines()[-1])
    return loaded


def test_worker_import_loads_no_egress_or_jsonschema() -> None:
    assert _loaded_by(PROBE) == []


def test_package_import_alone_loads_no_public_modules() -> None:
    code = (
        "import json, sys; import asfb; "
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith("
        "('jsonschema', 'asfb.egress', 'asfb.api')))))"
    )
    assert _loaded_by(code) == []


def test_public_names_load_on_first_use() -> None:
    from asfb.api import sanitize
    from asfb.errors import AsfbError, ErrorCode
    from asfb.types.key import SessionKey

    assert asfb.sanitize is sanitize
    assert asfb.AsfbError is AsfbError
    assert asfb.ErrorCode is ErrorCode
    assert asfb.SessionKey is SessionKey
    assert set(asfb.__all__) <= set(dir(asfb))


def test_unknown_attribute_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'nothing_here'"):
        _ = asfb.nothing_here
