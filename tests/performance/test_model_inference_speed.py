"""
Micro-benchmarks for resolver (≤5 ms) & selector (≤2 ms) per call.

The tests are resilient to refactors:
 ▸ They look for an obvious callable attribute.
 ▸ They skip but do not fail when the module cannot be imported
   (e.g. model swapped out of tree); CI will surface the skip.
"""
import importlib, inspect, time, pytest, statistics

# -------- utilities ----------------------------------------------------------
def _best_callable(mod, candidates):
    for name in candidates:
        if hasattr(mod, name) and callable(getattr(mod, name)):
            fn = getattr(mod, name)
            # unwrap @lru_cache etc.
            return inspect.unwrap(fn)
    return None

def _avg_ms(fn, *args, runs=200, **kwargs):
    t0 = time.perf_counter()
    for _ in range(runs):
        fn(*args, **kwargs)
    return (time.perf_counter() - t0) * 1_000 / runs

# -------- resolver -----------------------------------------------------------
@pytest.mark.skipif(
    importlib.util.find_spec("gateway.resolver") is None,
    reason="resolver package not present")
def test_resolver_avg_latency():
    mod = importlib.import_module("gateway.resolver")
    fn  = _best_callable(mod, ["resolve_anchor",
                               "resolve_text",
                               "embed_text",
                               "encode"])
    if fn is None:
        pytest.skip("no resolver callable found")
    avg = _avg_ms(fn, "dummy text")
    assert avg <= 5, f"Resolver avg {avg:.3f} ms > 5 ms budget"

# -------- selector -----------------------------------------------------------
@pytest.mark.skipif(
    importlib.util.find_spec("gateway.selector") is None,
    reason="selector module not present")
def test_selector_avg_latency():
    selector = importlib.import_module("gateway.selector")
    fn = _best_callable(selector, ["score_evidence", "rank", "select"])
    if fn is None:
        pytest.skip("no selector callable found")

    dummy_ev = [{"id": f"ev-{i}", "text": "foo"} for i in range(10)]
    avg = _avg_ms(fn, dummy_ev)
    assert avg <= 2, f"Selector avg {avg:.3f} ms > 2 ms budget"
