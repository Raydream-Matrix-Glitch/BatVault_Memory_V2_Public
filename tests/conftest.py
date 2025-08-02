import os, time, requests, pytest

_DEFAULT = os.getenv("GATEWAY_URL", "http://localhost:8000")

def _wait(url: str, timeout=60, interval=1.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=2).status_code == 200:  # healthy
                return
        except requests.RequestException:
            pass
        time.sleep(interval)
    raise RuntimeError(f"Timed-out waiting for {url}")

@pytest.fixture(scope="session", autouse=True)
def _services_ready():
    _wait(f"{_DEFAULT}/readyz")

@pytest.fixture(scope="session")
def gw_url():
    return _DEFAULT
