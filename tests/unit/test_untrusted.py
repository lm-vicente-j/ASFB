import copy
import json
import pickle
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from asfb.errors import UntrustedAccessError
from asfb.types.untrusted import Untrusted, unwrap

CANARY = "cnry_0123456789abcdef"
REDACTED = "Untrusted(<redacted>)"
PERCENT_FORMAT = "%s"


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(str, id="str"),
        pytest.param(lambda u: format(u, ""), id="format"),
        pytest.param(lambda u: f"{u}", id="fstring"),
        pytest.param(lambda u: PERCENT_FORMAT % u, id="percent"),
        pytest.param(bool, id="bool"),
        pytest.param(lambda u: u == u, id="eq"),
        pytest.param(hash, id="hash"),
    ],
)
def test_content_access_raises(operation: Callable[[Any], object]) -> None:
    with pytest.raises(UntrustedAccessError) as info:
        operation(Untrusted(CANARY))
    assert CANARY not in str(info.value)
    assert info.value.args == ("E_UNTRUSTED_ACCESS",)
    assert isinstance(info.value, TypeError)


def test_repr_is_constant() -> None:
    rendered = repr(Untrusted(CANARY))
    assert rendered == REDACTED
    assert CANARY not in rendered


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_pickle_raises_without_secret(protocol: int) -> None:
    with pytest.raises(UntrustedAccessError) as info:
        pickle.dumps(Untrusted(CANARY), protocol=protocol)
    assert CANARY not in str(info.value)


def test_copy_returns_same_object() -> None:
    value = Untrusted(CANARY)
    assert copy.copy(value) is value
    assert copy.deepcopy(value) is value


def test_setattr_raises() -> None:
    value = Untrusted(CANARY)
    name = "_value"
    with pytest.raises(UntrustedAccessError):
        setattr(value, name, "replaced")


def test_delattr_raises() -> None:
    value = Untrusted(CANARY)
    name = "_value"
    with pytest.raises(UntrustedAccessError):
        delattr(value, name)


def test_reinitialization_raises() -> None:
    value = Untrusted(CANARY)
    with pytest.raises(UntrustedAccessError) as info:
        value.__init__("replaced")
    assert "replaced" not in str(info.value)
    assert CANARY not in str(info.value)
    assert unwrap(value) == CANARY


def test_subclassing_raises() -> None:
    base: Any = Untrusted
    with pytest.raises(UntrustedAccessError):
        type("Sub", (base,), {})


def test_json_dumps_raises_without_secret() -> None:
    with pytest.raises(TypeError) as info:
        json.dumps(Untrusted(CANARY))
    assert CANARY not in str(info.value)


def _model_holding_untrusted() -> type[BaseModel]:
    class Holder(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        value: Untrusted[str]

    return Holder


def test_pydantic_cannot_hold_untrusted() -> None:
    with pytest.raises(UntrustedAccessError):
        _model_holding_untrusted()


def test_unwrap_returns_value() -> None:
    assert unwrap(Untrusted(CANARY)) == CANARY
