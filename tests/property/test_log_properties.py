"""Any generated log censors to a model whose clear text carries no date, and whose time buckets are
the bit counts of the deltas between consecutive timed records."""

import itertools
import re
import string
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from asfb.ingest import log
from asfb.sanitize.log import censor_log_with_pairs
from asfb.schema.doc import Profile
from asfb.types.key import SessionKey

_TOKEN = re.compile(r"t_[0-9a-f]{16}")
_BASE = datetime(2000, 1, 1, tzinfo=UTC)
STAMPS = st.integers(min_value=0, max_value=800_000_000).map(
    lambda seconds: _BASE + timedelta(seconds=seconds)
)
LEVELS = st.sampled_from(["INFO", "WARN", "ERROR", "DEBUG"])
WORDS = st.text(alphabet=string.ascii_letters + " ", min_size=1, max_size=24).filter(
    lambda text: text.strip() != ""
)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


@settings(max_examples=150, deadline=None)
@given(st.lists(st.tuples(STAMPS, LEVELS, WORDS), min_size=1, max_size=12))
def test_no_date_digits_reach_the_clear_output(rows: list[tuple[datetime, str, str]]) -> None:
    text = "".join(f"{_stamp(moment)} {level} {message}\n" for moment, level, message in rows)
    model, _pairs = censor_log_with_pairs(
        SessionKey.generate(), log.read_text(text, "text"), Profile.STRICT
    )
    clear = _TOKEN.sub("", model.model_dump_json())
    for moment, _level, _message in rows:
        assert str(moment.year) not in clear
        assert moment.strftime("%Y-%m-%d") not in clear


@settings(max_examples=150, deadline=None)
@given(st.lists(STAMPS, min_size=2, max_size=12))
def test_each_bucket_is_the_bit_count_of_its_delta(stamps: list[datetime]) -> None:
    text = "".join(f"{_stamp(moment)} INFO m\n" for moment in stamps)
    model, _pairs = censor_log_with_pairs(
        SessionKey.generate(), log.read_text(text, "text"), Profile.STRICT
    )
    assert model.records[0].time is None
    for (previous, current), record in zip(
        itertools.pairwise(stamps), model.records[1:], strict=True
    ):
        delta = int((current - previous).total_seconds() * 1000)
        assert record.time is not None
        assert record.time.index == min(abs(delta).bit_length(), 64)
        assert record.time.backwards is (delta < 0)
