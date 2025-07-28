from api_edge.app import healthz

def test_healthz():
    assert healthz()["ok"] is True
