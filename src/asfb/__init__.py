"""ASFB: show the agent what is public, censor what is yours.

The public names load on first use (PEP 562), so importing a submodule such as the isolated
worker does not pull in the API, egress or jsonschema.

`asfb.sanitize` is both the public function and the name of the sanitize subpackage. Importing
the subpackage sets that attribute to the module, so the package class ignores the import
machinery's assignment for this one name; the function then loads through __getattr__.
"""

import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from asfb.api import (
        Doc,
        sanitize,
        sanitize_html,
        sanitize_markdown,
        sanitize_python,
        sanitize_text,
    )
    from asfb.errors import AsfbError, ErrorCode
    from asfb.session import Session
    from asfb.types.key import SessionKey

__all__ = [
    "AsfbError",
    "Doc",
    "ErrorCode",
    "Session",
    "SessionKey",
    "sanitize",
    "sanitize_html",
    "sanitize_markdown",
    "sanitize_python",
    "sanitize_text",
]
_KEEP_FUNCTION: Final = frozenset({"sanitize"})


class _Package(ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        if name in _KEEP_FUNCTION and isinstance(value, ModuleType):
            return
        super().__setattr__(name, value)


def _exports() -> dict[str, object]:
    from asfb.api import (
        Doc,
        sanitize,
        sanitize_html,
        sanitize_markdown,
        sanitize_python,
        sanitize_text,
    )
    from asfb.errors import AsfbError, ErrorCode
    from asfb.session import Session
    from asfb.types.key import SessionKey

    return {
        "AsfbError": AsfbError,
        "Doc": Doc,
        "ErrorCode": ErrorCode,
        "Session": Session,
        "SessionKey": SessionKey,
        "sanitize": sanitize,
        "sanitize_html": sanitize_html,
        "sanitize_text": sanitize_text,
        "sanitize_markdown": sanitize_markdown,
        "sanitize_python": sanitize_python,
    }


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module 'asfb' has no attribute {name!r}")
    value = _exports()[name]
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})


sys.modules[__name__].__class__ = _Package
