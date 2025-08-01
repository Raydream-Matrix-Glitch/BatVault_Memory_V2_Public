"""
core_metrics – tiny helpers so services can record counters / histograms
without taking a hard dependency on the OpenTelemetry API.  When OTEL isn’t
configured we silently degrade to a no-op, which keeps unit-tests and local
dev friction-free.  In production we still emit both OTLP and Prometheus
metrics so the collector or Prometheus server can consume them.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, TYPE_CHECKING

# ── Prometheus fallback (used for /metrics endpoint) ────────────────────────
if TYPE_CHECKING:  # real classes visible only to the type checker
    from prometheus_client import Counter as PromCounter
    from prometheus_client import Histogram as PromHistogram

try:
    from prometheus_client import Counter as _pCounter, Histogram as _pHistogram
except ImportError:  # pragma: no cover – guarded by requirements/runtime.txt
    _pCounter = _pHistogram = None  # type: ignore[assignment]

_P_COUNTERS: Dict[str, "PromCounter"] = {}  # type: ignore[name-defined]
_P_HISTOS: Dict[str, "PromHistogram"] = {}  # type: ignore[name-defined]

# ── OpenTelemetry setup (preferred path) ────────────────────────────────────
try:
    from opentelemetry import metrics as _otel_metrics  # type: ignore
    from opentelemetry.sdk.metrics import MeterProvider  # type: ignore
    from opentelemetry.sdk.resources import Resource  # type: ignore

    if _otel_metrics.get_meter_provider() is None or isinstance(
        _otel_metrics.get_meter_provider(), _otel_metrics.NoOpMeterProvider  # type: ignore
    ):
        _otel_metrics.set_meter_provider(
            MeterProvider(resource=Resource.create({"service.name": "batvault"}))
        )

    _METER = _otel_metrics.get_meter("batvault.core_metrics", version="0.1.0")
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
            c.add(inc, attrs or {})
        except Exception:
            # Metrics must never break the request path
            pass

    # ── Prometheus record ───────────────────────────────────────────────────
    if _pCounter is not None:
        pc = _P_COUNTERS.get(name)
        if pc is None:
            pc = _pCounter(name, f"Counter for {name}")  # type: ignore[call-arg]
            _P_COUNTERS[name] = pc
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
            h.record(value, attrs or {})
        except Exception:
            pass

    # ── Prometheus record ───────────────────────────────────────────────────
    if _pHistogram is not None:
        ph = _P_HISTOS.get(name)
        if ph is None:
            ph = _pHistogram(name, f"Histogram for {name}")  # type: ignore[call-arg]
            _P_HISTOS[name] = ph
        ph.observe(value)


# Convenience alias for latency values
def histogram_ms(name: str, elapsed_ms: float, **attrs: Any) -> None:
    """Shortcut: record *elapsed_ms* (milliseconds) in histogram *name*."""
    histogram(name, elapsed_ms, **attrs)


__all__ = ["counter", "histogram", "histogram_ms"]
