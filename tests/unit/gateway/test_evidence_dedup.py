import asyncio
from contextlib import contextmanager

import httpx
import pytest

from gateway.evidence import EvidenceBuilder


@contextmanager
def patch_httpx_dupe(anchor_id: str, event_id: str):
    """
    Patch httpx.Client and httpx.AsyncClient so that calls to the Memory API
    return duplicate events for the same ID.  The first event has no relation
    and the second has an explicit `LED_TO` relation.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        # stub expand_candidates to return two identical events
        if p == "/api/graph/expand_candidates" and req.method == "POST":
            return httpx.Response(
                200,
                json={
                    "neighbors": [
                        {"type": "event", "id": event_id, "edge": {"rel": None}},
                        {"type": "event", "id": event_id, "edge": {"rel": "LED_TO"}},
                    ]
                },
            )
        # anchor enrichment
        if p == f"/api/enrich/decision/{anchor_id}" and req.method == "GET":
            return httpx.Response(200, json={"id": anchor_id, "supported_by": [event_id]})
        # event enrichment
        if p == f"/api/enrich/event/{event_id}" and req.method == "GET":
            return httpx.Response(200, json={"id": event_id, "led_to": [anchor_id]})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)

    # Capture original Client classes or callables.  In some test
    # environments `httpx.AsyncClient` or `httpx.Client` may already be
    # monkey‑patched to a function rather than a class.  Detect this
    # scenario and wrap accordingly to avoid TypeError when attempting
    # to subclass a function (see failing tests where a string is
    # interpreted as code).
    original_async = httpx.AsyncClient
    original_sync  = httpx.Client

    # Build wrapped subclasses or wrappers that always use our transport.
    # When the original symbol is a class we subclass it to inject the
    # transport.  When it is a function (e.g. a factory), we wrap it with
    # another function that forwards all arguments and sets the transport.
    if isinstance(original_async, type):
        class PatchedAsync(original_async):  # type: ignore[misc]
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("transport", transport)
                super().__init__(*args, **kwargs)
    else:
        def PatchedAsync(*args, **kwargs):  # type: ignore[misc, assignment]
            kwargs.setdefault("transport", transport)
            return original_async(*args, **kwargs)

    if isinstance(original_sync, type):
        class PatchedSync(original_sync):  # type: ignore[misc]
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("transport", transport)
                super().__init__(*args, **kwargs)
    else:
        def PatchedSync(*args, **kwargs):  # type: ignore[misc, assignment]
            kwargs.setdefault("transport", transport)
            return original_sync(*args, **kwargs)

    httpx.AsyncClient = PatchedAsync  # type: ignore[assignment]
    httpx.Client      = PatchedSync   # type: ignore[assignment]
    try:
        yield
    finally:
        httpx.AsyncClient = original_async  # type: ignore[assignment]
        httpx.Client      = original_sync   # type: ignore[assignment]


@pytest.mark.asyncio
async def test_evidence_builder_deduplicates_events():
    """
    EvidenceBuilder must de-duplicate events returned by the Memory API.
    When the Memory API returns multiple neighbours with the same event ID,
    the builder should only include a single instance in the final evidence.
    """
    anchor_id = "anchor-x"
    event_id = "event-y"
    builder = EvidenceBuilder(redis_client=None)
    # Patch httpx to serve duplicate events for the same ID
    with patch_httpx_dupe(anchor_id, event_id):
        evidence = await builder.build(anchor_id)
    # Only one event should remain after de-duplication
    assert len(evidence.events) == 1, f"Expected 1 event, got {len(evidence.events)}"
    assert evidence.events[0]["id"] == event_id
    # allowed_ids should include exactly the anchor and the event
    assert sorted(evidence.allowed_ids) == sorted([anchor_id, event_id])