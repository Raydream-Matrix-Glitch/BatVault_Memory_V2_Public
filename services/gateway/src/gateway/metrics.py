from typing import Any
from contextlib import contextmanager

from core_metrics import counter as _counter, histogram as _histogram
from core_logging import trace_span as _trace_span

__all__ = ["counter", "histogram", "span", "gateway_llm_requests", "gateway_llm_latency_ms"]


def counter(name: str, value: float, **attrs: Any) -> None:          # pragma: no-cover
    _counter(name, value, service="gateway", **attrs)


def histogram(name: str, value: float, **attrs: Any) -> None:        # pragma: no-cover
    _histogram(name, value, service="gateway", **attrs)


@contextmanager
def span(name: str, **attrs: Any):                                   # pragma: no-cover
    """Usage:  with metrics.span("expand_candidates"):"""
    with _trace_span(f"gateway.{name}", **attrs):
        yield

# ---------------------------------------------------------------------------
# LLM metrics (Milestone-7)
#
# Expose counters and histograms for LLM invocations.  These are
# lazily created via core_metrics and therefore safe to import before
# Prometheus is configured.  The helper functions defined above add
# the service label automatically.

def gateway_llm_requests(model: str, canary: str, inc: float = 1.0) -> None:
    """Increment the total number of LLM requests for the given model/canary."""
    _counter(
        "gateway_llm_requests",
        inc,
        service="gateway",
        model=model,
        canary=canary,
    )


def gateway_llm_latency_ms(model: str, canary: str, value: float) -> None:
    """Observe the latency histogram for an LLM call."""
    _histogram(
        "gateway_llm_latency_ms",
        value,
        service="gateway",
        model=model,
        canary=canary,
    )