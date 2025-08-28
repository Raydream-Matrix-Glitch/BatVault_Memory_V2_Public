import asyncio

from core_logging import get_logger, log_stage


def test_log_stage_imperative():
    """
    Old call-sites still work (returns a decorator object, but we ignore it).
    """
    logger = get_logger("test-log-stage")
    log_stage(
        logger,
        "unit",
        "event",
        request_id="abc123",
        snapshot_etag="etag",
        prompt_fingerprint="pf",
    )


def test_log_stage_decorator_sync():
    logger = get_logger("test-log-stage-sync")

    @log_stage(logger, "unit", "event.sync")
    def add(a, b):
        return a + b

    assert add(2, 3) == 5


def test_log_stage_decorator_async():
    logger = get_logger("test-log-stage-async")

    @log_stage(logger, "unit", "event.async")
    async def mul(a, b):
        return a * b

    result = asyncio.run(mul(4, 5))
    assert result == 20


def test_log_stage_context_manager():
    logger = get_logger("test-log-stage-ctx")
    with log_stage(logger, "unit", "event.ctx").ctx(extra="value"):
        # nothing to assert; absence of crash/log spam is success
        pass