from core_logging import get_logger, log_stage

def test_log_stage_runs():
    logger = get_logger("test-log-stage")
    # Just ensure it doesn't crash and accepts deterministic fields
    log_stage(logger, "unit", "event", request_id="abc123", snapshot_etag="etag", prompt_fingerprint="pf")
