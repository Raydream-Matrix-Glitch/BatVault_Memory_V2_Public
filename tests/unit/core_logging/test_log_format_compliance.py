import io, json
from contextlib import redirect_stdout


from core_logging import get_logger, log_stage


def test_log_envelope_compliance():
    """The log record must match the B5 envelope and nest stage‑specific metrics under *meta*."""

    buf = io.StringIO()

    logger = get_logger("gateway.unit_test")

    # Redirect the logger's StreamHandler (stdout) to our buffer
    with redirect_stdout(buf):
        log_stage(
            logger,
            "bundle",
            "bundle_complete",
            request_id="req123",
            snapshot_etag="snap123",
            selector_truncation=True,
            total_neighbors_found=12,
            final_evidence_count=8,
            dropped_evidence_ids=["evt1"],
            bundle_size_bytes=7680,
            max_prompt_bytes=8192,
        )

    raw = buf.getvalue().strip()
    assert raw, "No log output captured"

    # The buffer may now contain *multiple* JSON log lines emitted by the
    # Memory-API / Gateway fixtures that run in the background.  Scan each
    # line and pick the one that was produced by the logger under test
    # (service starts with “gateway”).
    payload = None
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # Ignore any non-JSON noise (e.g. uvicorn access logs)
            continue
        if rec.get("service", "").startswith("gateway"):
            payload = rec
            break

    assert payload is not None, "Expected gateway log record not found in captured output"

    # ── top‑level ─────────────────────────────────────────────────────────
    assert "timestamp" in payload, "timestamp missing"
    assert payload["level"] == "INFO"
    assert payload["service"].startswith("gateway"), "service field incorrect"
    assert payload["stage"] == "bundle", "stage field incorrect"
    assert payload["request_id"] == "req123"
    assert payload["snapshot_etag"] == "snap123"

    # ── meta ──────────────────────────────────────────────────────────────
    meta = payload.get("meta")
    assert meta, "meta object missing"
    assert meta["selector_truncation"] is True
    assert meta["total_neighbors_found"] == 12
    assert meta["final_evidence_count"] == 8
    assert meta["dropped_evidence_ids"] == ["evt1"]
    assert meta["bundle_size_bytes"] == 7680
    assert meta["max_prompt_bytes"] == 8192