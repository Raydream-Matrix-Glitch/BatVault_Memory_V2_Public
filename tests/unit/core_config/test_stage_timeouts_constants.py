from core_config.constants import (
    TIMEOUT_SEARCH_MS,
    TIMEOUT_EXPAND_MS,
    TIMEOUT_ENRICH_MS,
)
from core_config.settings import get_settings


def test_stage_timeouts_match_settings_defaults():
    """Settings defaults must mirror the shared constants."""
    s = get_settings()
    assert s.timeout_search_ms == TIMEOUT_SEARCH_MS
    assert s.timeout_expand_ms == TIMEOUT_EXPAND_MS
    assert s.timeout_enrich_ms == TIMEOUT_ENRICH_MS