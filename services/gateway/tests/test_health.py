from gateway.app import healthz
def test_healthz():
    assert healthz()["ok"] is True
