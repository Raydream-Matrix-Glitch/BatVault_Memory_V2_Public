from typing import Any
from contextlib import contextmanager

from core_metrics import counter as _counter, histogram as _histogram
from core_logging import trace_span as _trace_span

__all__ = ["counter", "histogram", "span"]


def counter(name: str, value: float, **attrs: Any) -> None:          # pragma: no-cover
    _counter(name, value, service="gateway", **attrs)


def histogram(name: str, value: float, **attrs: Any) -> None:        # pragma: no-cover
    _histogram(name, value, service="gateway", **attrs)


@contextmanager
def span(name: str, **attrs: Any):                                   # pragma: no-cover
    """Usage:  with metrics.span("expand_candidates"):"""
    with _trace_span(f"gateway.{name}", **attrs):
        yield