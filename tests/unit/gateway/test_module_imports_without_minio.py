import importlib, os

def test_gateway_module_imports_without_minio(monkeypatch):
    # Ensure artefact writes are disabled
    monkeypatch.setenv("DISABLE_ARTEFACT_WRITES", "1")
    # Unset MinIO env to simulate missing dependency
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
    # Module should import without MinIO installed due to lazy import
    importlib.import_module("gateway.app")