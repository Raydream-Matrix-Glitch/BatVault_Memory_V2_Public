# tests/core/test_fingerprint.py
import json
import pathlib
import pytest

from core_utils.fingerprints import prompt_fingerprint

HERE = pathlib.Path(__file__).parent
REPO_ROOT = HERE.parents[2]   # go up from tests/core → tests → repo root
FIXTURE = (
    REPO_ROOT
    / "memory"
    / "fixtures"
    / "decisions"
    / "decision_min.json"
)

with FIXTURE.open(encoding="utf-8") as fh:
    ENVELOPE = json.load(fh)

EXPECTED_FINGERPRINT = "0d6cb4d5fe2e4e27cfcd8e275ef16d8df3de6f0c0b0cb7d7a14cfb9cdd6b8f7b"

def test_canonical_fingerprint_is_stable():
    fp1 = prompt_fingerprint(ENVELOPE)
    fp2 = prompt_fingerprint(ENVELOPE)
    assert fp1 == fp2, "Fingerprint should be stable across calls"
    assert fp1 == EXPECTED_FINGERPRINT, "Fingerprint must match the expected value"

def test_canonical_fingerprint_independent_instances():
    with FIXTURE.open(encoding="utf-8") as fh2:
        envelope2 = json.load(fh2)
    assert prompt_fingerprint(envelope2) == EXPECTED_FINGERPRINT
