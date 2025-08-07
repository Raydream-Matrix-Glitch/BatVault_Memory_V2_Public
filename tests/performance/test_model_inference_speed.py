# tests/performance/test_model_inference_speed.py
"""
Performance smoke-tests (non-legacy).

• **Resolver**: `gateway.resolver.resolve_decision_text` must respond in ≤ 5 ms.
• **Selector**: `gateway.selector.truncate_evidence` must respond in ≤ 2 ms.

The helper automatically handles sync/async callables.
Latency budgets can be overridden via `services.config.performance`.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import time
import pytest

# ────────────────────────── performance budgets ─────────────────────────── #

try:
    perf_cfg = importlib.import_module("services.config.performance")
    RESOLVER_BUDGET_MS: float = getattr(perf_cfg, "RESOLVER_INFERENCE_MS", 5.0)
    SELECTOR_BUDGET_MS: float = getattr(perf_cfg, "SELECTOR_INFERENCE_MS", 2.0)
except ModuleNotFoundError:
    RESOLVER_BUDGET_MS, SELECTOR_BUDGET_MS = 5.0, 2.0

# ─────────────────────────────── helpers ────────────────────────────────── #


def _find_callable(module, names: list[str]):
    """Return the first attribute in *module* whose name matches one in *names*."""
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return inspect.unwrap(fn)
    return None


def _avg_latency_ms(fn, *args, runs: int | None = None, **kwargs) -> float:
    """Average runtime of *fn* in **milliseconds** across *runs* invocations."""
    if inspect.iscoroutinefunction(fn):
        runs = runs or 50  # amortise event-loop start-up
        async def _bench():
            start = time.perf_counter()
            for _ in range(runs):
                await fn(*args, **kwargs)
            return (time.perf_counter() - start) * 1_000 / runs
        return asyncio.run(_bench())

    runs = runs or 200
    start = time.perf_counter()
    for _ in range(runs):
        fn(*args, **kwargs)
    return (time.perf_counter() - start) * 1_000 / runs

# ────────────────────────────── resolver ────────────────────────────────── #


@pytest.mark.skipif(
    importlib.util.find_spec("gateway.resolver") is None,
    reason="gateway.resolver package not present",
)
def test_resolver_avg_latency():
    mod = importlib.import_module("gateway.resolver")
    fn = _find_callable(mod, ["resolve_decision_text"])
    if fn is None:
        pytest.skip("resolver callable 'resolve_decision_text' not found")

    avg = _avg_latency_ms(fn, "Why did Panasonic exit plasma?")
    assert avg <= RESOLVER_BUDGET_MS, (
        f"Resolver avg {avg:.3f} ms > {RESOLVER_BUDGET_MS} ms budget"
    )

# ────────────────────────────── selector ────────────────────────────────── #


@pytest.mark.skipif(
    importlib.util.find_spec("gateway.selector") is None,
    reason="gateway.selector module not present",
)
def test_selector_avg_latency():
    selector_mod = importlib.import_module("gateway.selector")
    fn = _find_callable(selector_mod, ["truncate_evidence"])
    if fn is None:
        pytest.skip("selector callable 'truncate_evidence' not found")

    # Build minimal valid WhyDecisionEvidence instance
    core_models = importlib.import_module("core_models.models")
    WhyDecisionAnchor = getattr(core_models, "WhyDecisionAnchor")
    WhyDecisionEvidence = getattr(core_models, "WhyDecisionEvidence")

    dummy_anchor = WhyDecisionAnchor(id="anchor-1")
    dummy_evidence = WhyDecisionEvidence(anchor=dummy_anchor)

    avg = _avg_latency_ms(fn, dummy_evidence)
    assert avg <= SELECTOR_BUDGET_MS, (
        f"Selector avg {avg:.3f} ms > {SELECTOR_BUDGET_MS} ms budget"
    )
