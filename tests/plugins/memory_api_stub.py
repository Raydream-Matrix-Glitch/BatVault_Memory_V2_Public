
"""Pytest plugin shim kept for backward compatibility.

The original heavy‑weight Memory‑API stub has been replaced by the
ultra‑light solution in *conftest.py*.  We register a dummy plugin so
that pytest's `-p tests.plugins.memory_api_stub` command‑line option
remains valid without extra maintenance.
"""
def pytest_configure(config):  # noqa: D401
    # Nothing to configure – conftest.py already patched httpx.
    pass
