"""Shared pydantic base for closed, strict models."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Forbids extra fields and coercion; instances are immutable."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
        allow_inf_nan=False,
    )
