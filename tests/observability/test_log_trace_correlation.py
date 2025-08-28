import json
import logging
from core_logging import get_logger, trace_span

def test_logs_carry_trace_ids(monkeypatch, capsys):
    logger = get_logger("test")
    with trace_span("unit", stage="test"):
        logger.info("hello", extra={"request_id": "req_demo"})
    out = capsys.readouterr().out.strip()
    rec = json.loads(out)
    assert "trace_id" in rec and isinstance(rec["trace_id"], str) and rec["trace_id"] != ""
    assert "span_id" in rec and isinstance(rec["span_id"], str) and rec["span_id"] != ""