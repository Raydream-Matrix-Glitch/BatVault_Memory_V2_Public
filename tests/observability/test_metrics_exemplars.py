import types
import core_metrics
from core_logging import trace_span

def test_histogram_attaches_exemplar_when_trace_present(monkeypatch):
    # Fake the prometheus histogram class so we can capture .observe kwargs
    calls = {}
    class FakeHist:
        def __init__(self, *a, **k): pass
        def observe(self, value, **kwargs):
            calls['kwargs'] = kwargs
    # Ensure core_metrics uses our fake histogram instance
    monkeypatch.setattr(core_metrics, "_pHistogram", FakeHist)
    # Clear any cache to force a new FakeHist
    core_metrics._P_HISTOS.pop("demo_ttfb_seconds", None)

    # Provide a fake OTEL current span with a trace id
    class _Ctx: trace_id = int("1234", 16)
    class _Span:
        def get_span_context(self): return _Ctx()
    fake_trace = types.SimpleNamespace(get_current_span=lambda: _Span())

    # Monkeypatch opentelemetry.trace to our fake
    import sys
    otel = sys.modules.get("opentelemetry")
    if otel is None:
        otel = types.SimpleNamespace()
        sys.modules["opentelemetry"] = otel
    otel.trace = fake_trace

    # Within a trace_span (bridges into OTEL when available), record a histogram
    with trace_span("unit_test", stage="test"):
        core_metrics.histogram("demo_ttfb_seconds", 0.123)

    # Assert that the exemplar was attempted (kwargs include exemplar with trace_id)
    assert 'kwargs' in calls, "histogram.observe not invoked"
    ex = calls['kwargs'].get('exemplar', {})
    assert isinstance(ex, dict) and 'trace_id' in ex and len(ex['trace_id']) >= 4