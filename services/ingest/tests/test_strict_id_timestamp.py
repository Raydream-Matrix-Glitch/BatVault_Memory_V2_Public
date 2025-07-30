import re
from ingest.cli import ID_RE, TS_RE

def test_id_regex_ok():
    assert ID_RE.match("panasonic-automotive-infotainment-acquisition-2014")
    assert not ID_RE.match("Bad_ID")
    assert not ID_RE.match("ab")  # too short

def test_ts_regex_ok():
    assert TS_RE.match("2024-07-20T14:30:00Z")
    assert TS_RE.match("2024-07-20T14:30:00.123Z")
    assert not TS_RE.match("2024-07-20 14:30:00Z")
    assert not TS_RE.match("2024-07-20T14:30:00+00:00")
