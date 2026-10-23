"""Write the committed JSON Schema files: python -m asfb.schema."""

import json
from pathlib import Path

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

_MODELS = (
    (SCHEMA_RESOURCE, DocV1),
    (LIST_RESOURCE, ListV1),
    (DIFF_RESOURCE, DiffV1),
    (RUN_RESOURCE, RunV1),
    (TEXT_RESOURCE, TextV1),
    (MD_RESOURCE, MdV1),
    (CODE_RESOURCE, CodeV1),
    (LOG_RESOURCE, LogV1),
    (DB_RESOURCE, DbV1),
)


def main() -> None:
    for resource, model in _MODELS:
        text = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        path = Path(__file__).with_name(resource)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)


if __name__ == "__main__":
    main()
