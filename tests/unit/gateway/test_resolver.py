import pytest

from gateway.resolver import resolve_decision_text


@pytest.mark.asyncio
async def test_resolver_stub():
    out = await resolve_decision_text("nonexistent query – should fallback")
    assert out is None or isinstance(out, dict)