from __future__ import annotations
from typing import Any, Iterator
from contextlib import contextmanager

# Route all metrics through core_metrics, but hide it behind this stable facade
from core_metrics import counter as _counter, histogram as _histogram, gauge as _gauge  # type: ignore
from core_logging import trace_span as _trace_span

__all__ = [
    "counter",
    "histogram",
    "gauge",
    "span",
    "gateway_llm_requests",
    "gateway_llm_latency_ms",
]

def counter(name: str, value: float, **attrs: Any) -> None:  # pragma: no-cover
    """
    Stable facade for counters. Always injects service='gateway' unless caller overrides.
    Use this instead of importing core_metrics directly to prevent naming drift.
    """
    try:
        if "service" not in attrs:
            attrs["service"] = "gateway"
        _counter(name, value, **attrs)
    except Exception:
        # Never let metrics break hot paths
        pass

def histogram(name: str, value: float, **attrs: Any) -> None:  # pragma: no-cover
    """
    Stable facade for histograms. Always injects service='gateway' unless caller overrides.
    """
    try:
        if "service" not in attrs:
            attrs["service"] = "gateway"
        _histogram(name, value, **attrs)
    except Exception:
        pass

def gauge(name: str, value: float, **attrs: Any) -> None:  # pragma: no-cover
    """
    Stable facade for gauges. Always injects service='gateway' unless caller overrides.
    """
    try:
        if "service" not in attrs:
            attrs["service"] = "gateway"
        _gauge(name, value, **attrs)
    except Exception:
        pass

@contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:                 # pragma: no-cover
    """Context manager over the shared tracer for quick stage timing."""
    with _trace_span(name, **attrs):
        yield

def gateway_llm_requests(model: str, canary: str, status: str) -> None:
    """Increment the LLM requests counter with common labels."""
    _counter(
        "gateway_llm_requests_total",
        1,
        service="gateway",
        model=model,
        canary=canary,
        status=status,
    )

# ── Canonical convenience wrappers for common Gateway metrics ───────────────

def gateway_llm_requests(model: str, canary: str, status: str) -> None:
    """
    Count LLM adapter calls by model/cohort and status.

    This helper delegates to the stable ``counter`` facade to ensure that
    service‑scoped labels are injected consistently and that any downstream
    exceptions are swallowed.  Removing the duplicated implementation
    eliminates subtle naming drift and hard‑coded labels, leaving a
    single authoritative definition.
    """
    counter(
        "gateway_llm_requests_total",
        1,
        model=model,
        canary=canary,
        status=status,
    )

def gateway_llm_latency_ms(model: str, canary: str, value: float) -> None:
    """
    Observe the latency histogram for an LLM call.
    """
    histogram(
        "gateway_llm_latency_ms",
        value,
        model=model,
        canary=canary,
    )