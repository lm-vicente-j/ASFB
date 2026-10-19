"""Write the committed JSON Schema file: python -m asfb.schema."""

import json
from pathlib import Path

from asfb.schema import SCHEMA_RESOURCE
from asfb.schema.doc import DocV1


def main() -> None:
    text = json.dumps(DocV1.model_json_schema(), indent=2, sort_keys=True) + "\n"
    path = Path(__file__).with_name(SCHEMA_RESOURCE)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


if __name__ == "__main__":
    main()
