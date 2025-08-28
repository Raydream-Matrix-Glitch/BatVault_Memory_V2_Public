"""
core_metrics – tiny helpers so services can record counters / histograms
without taking a hard dependency on the OpenTelemetry API.  When OTEL isn’t
configured we silently degrade to a no-op, which keeps unit-tests and local
dev friction-free.  In production we still emit both OTLP and Prometheus
metrics so the collector or Prometheus server can consume them.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, TYPE_CHECKING, cast
import time as _time

# ── Prometheus fallback (used for /metrics endpoint) ────────────────────────
if TYPE_CHECKING:  # real classes visible only to the type checker
    from prometheus_client import Counter as PromCounter
    from prometheus_client import Histogram as PromHistogram
    from prometheus_client import Gauge as PromGauge

try:
    from prometheus_client import (
        REGISTRY as _PROM_REGISTRY,
        Counter as _pCounter,
        Histogram as _pHistogram,
        Gauge as _pGauge,
    )
except ImportError:  # pragma: no cover – guarded by requirements/runtime.txt
    _PROM_REGISTRY = None                          # type: ignore[assignment]
    _pCounter = _pHistogram = _pGauge = None       # type: ignore[assignment]

_P_COUNTERS: Dict[str, "PromCounter"] = {}  # type: ignore[name-defined]
_P_HISTOS: Dict[str, "PromHistogram"] = {}  # type: ignore[name-defined]
_P_GAUGES: Dict[str, "PromGauge"] = {}      # type: ignore[name-defined]

# ── OpenTelemetry setup (preferred path) ────────────────────────────────────
try:
    from opentelemetry import metrics as _otel_metrics  # type: ignore
    from opentelemetry.sdk.metrics import MeterProvider  # type: ignore
    from opentelemetry.sdk.resources import Resource  # type: ignore

    if _otel_metrics.get_meter_provider() is None or isinstance(
        _otel_metrics.get_meter_provider(), _otel_metrics.NoOpMeterProvider  # type: ignore
    ):
        import os as _os
        _svc = _os.getenv("OTEL_SERVICE_NAME") or _os.getenv("SERVICE_NAME") or "batvault"
        _otel_metrics.set_meter_provider(
            MeterProvider(resource=Resource.create({"service.name": _svc}))
        )
    import os as _os
    _svc2 = _os.getenv("OTEL_SERVICE_NAME") or _os.getenv("SERVICE_NAME") or "batvault"
    _METER = _otel_metrics.get_meter(f"{_svc2}.core_metrics", version="0.1.0")
except Exception:  # pragma: no cover – OTEL missing or mis-configured
    _METER = None  # type: ignore[assignment]

_COUNTERS: Dict[str, Any] = {}
_HISTOS: Dict[str, Any] = {}
_LOCK = threading.Lock()

# --------------------------------------------------------------------------- #
# Public helpers                                                              #
# --------------------------------------------------------------------------- #
def counter(name: str, inc: int | float = 1, **attrs: Any) -> None:
    """
    Increment *name* by *inc* (default 1).

    The function writes to OTLP (if available) **and** to a Prometheus
    in-process registry, ensuring the metric shows up at `/metrics` even when
    OTEL is disabled (e.g. local dev, CI).
    """
    # ── OTEL record ─────────────────────────────────────────────────────────
    if _METER is not None:
        with _LOCK:
            c = _COUNTERS.get(name) or _METER.create_counter(name)
            _COUNTERS[name] = c
        try:
            c.add(inc, attributes=attrs or {})
        except Exception:
            # Metrics must never break the request path
            pass

    # ── Prometheus record ───────────────────────────────────────────────────
    if _pCounter is not None:
        pc = _P_COUNTERS.get(name)
        if pc is None:
            existing = None if _PROM_REGISTRY is None else _PROM_REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]
            pc = cast("PromCounter", existing) if existing is not None else _pCounter(name, f"Counter for {name}")  # type: ignore[call-arg]
            _P_COUNTERS[name] = pc
        # Try Prometheus exemplars with current trace_id if supported
        try:
            labels = {}
            try:
                from opentelemetry import trace as _t  # type: ignore
                _sp = _t.get_current_span()
                if _sp:
                    _ctx = _sp.get_span_context()  # type: ignore[attr-defined]
                    if getattr(_ctx, "trace_id", 0):
                        labels = {"trace_id": f"{_ctx.trace_id:032x}"}
            except Exception:
                labels = {}
            pc.inc(inc, exemplar=labels)  # type: ignore[call-arg]
        except TypeError:
            pc.inc(inc)


def histogram(name: str, value: float, **attrs: Any) -> None:
    """
    Record *value* in histogram *name*.
    """
    # ── OTEL record ─────────────────────────────────────────────────────────
    if _METER is not None:
        with _LOCK:
            h = _HISTOS.get(name) or _METER.create_histogram(name)
            _HISTOS[name] = h
        try:
            h.record(value, attributes=attrs or {})
        except Exception:
            pass

    # ── Prometheus record ───────────────────────────────────────────────────
    if _pHistogram is not None:
        ph = _P_HISTOS.get(name)
        if ph is None:
            existing = None if _PROM_REGISTRY is None else _PROM_REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]
            ph = cast("PromHistogram", existing) if existing is not None else _pHistogram(name, f"Histogram for {name}")  # type: ignore[call-arg]
            _P_HISTOS[name] = ph
        try:
            labels = {}
            try:
                from opentelemetry import trace as _t  # type: ignore
                _sp = _t.get_current_span()
                if _sp:
                    _ctx = _sp.get_span_context()  # type: ignore[attr-defined]
                    if getattr(_ctx, "trace_id", 0):
                        labels = {"trace_id": f"{_ctx.trace_id:032x}"}
            except Exception:
                labels = {}
            ph.observe(value, exemplar=labels)  # type: ignore[call-arg]
        except TypeError:
            ph.observe(value)


# Convenience alias for latency values
def histogram_ms(name: str, elapsed_ms: float, **attrs: Any) -> None:
    """Shortcut: record *elapsed_ms* (milliseconds) in histogram *name*."""
    histogram(name, elapsed_ms, **attrs)

# --------------------------------------------------------------------------- #
# Gauge helper – gives us an unsuffixed metric line for CI checks             #
# --------------------------------------------------------------------------- #
def gauge(name: str, value: float, **attrs: Any) -> None:
    """
    Record *value* in Prometheus **Gauge** *name*.

    Gauges expose the plain metric line (e.g. ``api_edge_ttfb_seconds``) that
    the CI suite validates, while histograms still provide latency percentiles.
    """
    if _pGauge is None:     # Prometheus library missing
        return

    g = _P_GAUGES.get(name)
    if g is None:
        existing = None if _PROM_REGISTRY is None else _PROM_REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]
        g = cast("PromGauge", existing) if existing is not None else _pGauge(name, f"Gauge for {name}")  # type: ignore[call-arg]
        _P_GAUGES[name] = g
    g.set(value)


__all__ = ["counter", "histogram", "histogram_ms", "gauge"]

def record_latency_ms(metric_base: str, t0: float, **attrs: Any) -> float:
    """
    Convenience: record elapsed time since *t0* as
      • histogram:  {metric_base}_latency_ms
      • gauge:      {metric_base}_latency_ms_latest
    Returns the measured latency in ms.
    """
    dt_ms = (_time.perf_counter() - t0) * 1000.0
    try:
        histogram_ms(f"{metric_base}_latency_ms", dt_ms, **attrs)
        gauge(f"{metric_base}_latency_ms_latest", dt_ms)
    except Exception:
        pass
    return dt_ms

__all__ += ["record_latency_ms"]