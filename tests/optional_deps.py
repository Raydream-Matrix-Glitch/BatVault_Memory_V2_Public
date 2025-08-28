"""
Ultra-light shims for *runtime-optional* libraries
––––––––––––––––––––––––––––––––––––––––––––––––––
Import **once** from the root-level ``conftest.py`` – the entire test-suite
then runs even on a vanilla Python install with *no* Redis/MinIO/Arango/FastAPI
wheels available.

Only FastAPI + orjson get a tiny behaviour-stub; every other package is a
black-hole ``_Lazy`` module that never raises AttributeError.
"""
from __future__ import annotations

import sys, types, json
from importlib.machinery import ModuleSpec

# ── generic black-hole module ────────────────────────────────────────────────
class _Lazy(types.ModuleType):
    def __init__(self, name: str):
        super().__init__(name)
        self.__path__: list[str] = []                       # looks like a package
        self.__spec__ = ModuleSpec(name, loader=None, is_package=True)
    def __getattr__(self, key):               # any attr → same stub
        return self
    __call__ = __getattr__                    # …and callable, too


def _register(name: str, mod: types.ModuleType) -> None:
    sys.modules[name] = mod
    if "." in name:                           # ensure parents exist
        parent, child = name.rsplit(".", 1)
        if parent not in sys.modules:
            _register(parent, _Lazy(parent))
        setattr(sys.modules[parent], child, mod)

# ── FastAPI mini-stub (just enough for decorator tests) ──────────────────────
fastapi = types.ModuleType("fastapi")
class _Resp:                                           # super-thin Response
    def __init__(self, body, status_code=200):
        self._b, self.status_code = body, status_code
    def json(self): return self._b

class _FastAPI:
    def __init__(self, *a, **k):
        self.routes: dict[tuple[str, str], callable] = {}
        self.middleware, self.state = [], types.SimpleNamespace()
    def _verb(self, v):
        def deco(path, **_):
            def wrap(fn): self.routes[(v, path)] = fn ; return fn
            return wrap
        return deco
    get = post = put = delete = patch = head = options = _verb
    def add_middleware(self, cls, *a, **k): self.middleware.append((cls, a, k))
    def middleware(self, *_): return lambda fn: fn
    def on_event(self, *_): return lambda fn: fn

tc_mod = types.ModuleType("fastapi.testclient")
tc_mod.TestClient = lambda app: types.SimpleNamespace(
    get=lambda path, **_: _Resp(
        app.routes.get(("GET", path), lambda: {"detail": "Not Found"})())
)
fastapi.FastAPI, fastapi.Response, fastapi.HTTPException = _FastAPI, _Resp, RuntimeError
fastapi.testclient = tc_mod
_register("fastapi", fastapi)
_register("fastapi.testclient", tc_mod)
_register("fastapi.exceptions", types.ModuleType("fastapi.exceptions"))

# ── every other *nice-to-have* → black-hole stub ────────────────────────────
for pkg in (
    "starlette", "starlette.middleware", "starlette.middleware.base",
    "starlette.requests", "redis", "python_arango", "minio", "minio.error",
    "torch", "numpy", "prometheus_client", "opentelemetry", "opentelemetry.sdk",
    "opentelemetry.trace", "opentelemetry.exporter", "sentence_transformers",
    "slowapi", "anyio",
):
    if pkg not in sys.modules:
        _register(pkg, _Lazy(pkg))

# ── deterministic orjson replacement (logger & fingerprints import it) ──────
orjson = types.ModuleType("orjson")
orjson.dumps = lambda o, *_, **__: json.dumps(o, sort_keys=True).encode()
orjson.loads = lambda b, *_, **__: json.loads(b if isinstance(b, str) else b.decode())
orjson.OPT_SORT_KEYS, orjson.OPT_OMIT_MICROSECONDS = 1, 2
_register("orjson", orjson)
