import os, socket, importlib
from fastapi.testclient import TestClient

def _random_closed_port() -> int:
    """Return a TCP port that is very likely closed (bind-then-close trick)."""
    s = socket.socket(); s.bind(("", 0)); port = s.getsockname()[1]; s.close(); return port

def test_resolve_text_falls_back_when_port_closed(monkeypatch):
    # Force ARANGO_URL to localhost:<closed-port> so DNS resolves but port is shut.
    port = _random_closed_port()
    monkeypatch.setenv("ARANGO_URL", f"http://127.0.0.1:{port}")

    # Reload storage layer to pick up the new env-var *before* app import.
    import core_storage.arangodb as arango_mod
    importlib.reload(arango_mod)
    from memory_api.app import app, _clear_store_cache
    _clear_store_cache()

    client = TestClient(app)
    r = client.post("/api/resolve/text", json={"q": "foo"})
    assert r.status_code == 200
    assert r.json()["matches"] == []