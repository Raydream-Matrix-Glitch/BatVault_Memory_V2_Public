import pytest
pytest.importorskip("pytest_asyncio")  # clean “SKIPPED – requires pytest-asyncio”

from gateway.resolver import resolve_decision_text


@pytest.mark.asyncio
async def test_resolver_stub():
    out = await resolve_decision_text("nonexistent query – should fallback")
    assert out is None or isinstance(out, dict)


@pytest.mark.asyncio
async def test_no_unawaited_coroutines(recwarn):
    # Ensure that resolve_decision_text does not produce unawaited-coroutine warnings
    await resolve_decision_text("cache miss")
    assert not any("was never awaited" in str(w.message) for w in recwarn)
