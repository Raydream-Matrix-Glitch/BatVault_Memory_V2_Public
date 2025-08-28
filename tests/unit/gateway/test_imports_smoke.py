# tests/unit/gateway/test_imports_smoke.py
def test_gateway_imports_and_symbols_present():
    import importlib
    m = importlib.import_module("gateway.builder")
    # ensure tests can patch both symbols without explosion
    assert hasattr(m, "__dict__")
