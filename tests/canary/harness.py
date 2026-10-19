"""Canary planting, encoded-variant leak detection and the M0 reference pipeline."""

import base64
import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from asfb.egress import Emitted, emit
from asfb.sanitize.tokens import tokenize
from asfb.schema.doc import DocV1, Profile, VocabRef
from asfb.types.key import SessionKey
from asfb.types.untrusted import Untrusted
from asfb.types.values import Bucket, EnumValue
from tests.stubs import STUB_VOCAB

PLACEHOLDER: Final = "@@CANARY@@"
CORPUS: Final = Path(__file__).resolve().parents[1] / "corpus" / "canary" / "carriers.json"


@dataclass(frozen=True, slots=True)
class Carrier:
    id: str
    template: str


def new_canary() -> str:
    return "cnry_" + secrets.token_hex(8)


def load_carriers() -> list[Carrier]:
    entries: list[dict[str, str]] = json.loads(CORPUS.read_text(encoding="utf-8"))
    return [Carrier(id=entry["id"], template=entry["template"]) for entry in entries]


def plant(template: str, canary: str) -> str:
    return template.replace(PLACEHOLDER, canary)


def variants(canary: str) -> list[str]:
    data = canary.encode("utf-8")
    hex_lower = data.hex()
    return [
        canary,
        canary.upper(),
        hex_lower,
        hex_lower.upper(),
        base64.b64encode(data).decode("ascii"),
    ]


def find_leaks(blobs: Iterable[str | bytes], canaries: Iterable[str]) -> list[str]:
    needles = sorted({form for canary in canaries for form in variants(canary)})
    leaks: list[str] = []
    for blob in blobs:
        data = blob.encode("utf-8", "surrogatepass") if isinstance(blob, str) else blob
        for needle in needles:
            if needle.encode("ascii") in data and needle not in leaks:
                leaks.append(needle)
    return leaks


def m0_pipeline(text: str, key: SessionKey) -> Emitted:
    token = tokenize(key, Untrusted(text))
    doc = DocV1(
        profile=Profile.STRICT,
        vocab=(VocabRef(id=STUB_VOCAB.id, version=STUB_VOCAB.version),),
        items=(token, EnumValue(vocab=STUB_VOCAB.id, term="btn"), Bucket.log2(len(text))),
    )
    return emit(doc, vocab={STUB_VOCAB.id: STUB_VOCAB})
