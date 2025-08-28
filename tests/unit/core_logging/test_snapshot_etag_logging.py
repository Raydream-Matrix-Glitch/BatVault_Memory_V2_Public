import logging

import pytest

from core_logging import get_logger, set_snapshot_etag


def _emit_stage_logs(logger):
    """Emit one INFO record for the stages we instrument today."""
    for stage in ("resolve", "plan", "exec", "bundle", "validate"):
        logger.info("unit-test-log", stage=stage)


def test_all_logs_include_snapshot_etag(caplog: pytest.LogCaptureFixture) -> None:
    snap = "unit-test-etag"
    set_snapshot_etag(snap)

    logger = get_logger("gateway.unit_test")

    with caplog.at_level(logging.INFO):
        _emit_stage_logs(logger)

    assert caplog.records, "Structured logging appears to be disabled."

    for record in caplog.records:
        # ① structlog adapter binds attributes directly …
        if hasattr(record, "snapshot_etag"):
            assert record.snapshot_etag == snap
        # ② … but some handlers serialize to JSON strings.
        else:
            payload: str = record.getMessage()
            assert snap in payload, f"`snapshot_etag` missing: {payload}"