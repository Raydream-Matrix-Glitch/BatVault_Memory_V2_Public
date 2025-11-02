#!/usr/bin/env python3
"""
Dump OpenAPI specs for Gateway and Memory into batvault_frontend/openapi/.
Use `bash scripts/ensure_codegen_venv.sh` in normal workflows.
If FastAPI isn't installed in the current interpreter, we exit with a clear hint.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Ensure local packages and services are importable
pkgs_dir = ROOT / "packages"
for p in pkgs_dir.glob("*/src"):
    sys.path.append(str(p))
# Preflight: ensure third-party deps used during import are present.
try:
    import fastapi as _fastapi  # noqa: F401
    import pydantic as _pydantic  # noqa: F401
    import httpx as _httpx  # noqa: F401
except Exception as e:
    sys.stderr.write(
        "[dump_openapi] Missing a required dependency ({}). "
        "Run: bash scripts/ensure_codegen_venv.sh\n".format(e.__class__.__name__)
    )
    sys.exit(1)
sys.path.append(str(ROOT / "services" / "gateway" / "src"))
sys.path.append(str(ROOT / "services" / "memory_api" / "src"))

out_dir = ROOT / "batvault_frontend" / "openapi"
out_dir.mkdir(parents=True, exist_ok=True)

def _normalize(obj):
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(x) for x in obj]
    return obj

def _build_openapi(app, title: str, version: str) -> dict:
    """
    Build a valid OpenAPI 3.x dict from a FastAPI app, even if app.openapi()
    is missing/odd. Prefer app.openapi(); fall back to get_openapi(...).
    """
    spec = None
    try:
        spec = app.openapi()
    except Exception:
        spec = None
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception:
            spec = None
    if not isinstance(spec, dict) or "openapi" not in spec:
        # Lazy import here so we can show a helpful message if FastAPI isn't installed
        try:
            from fastapi.openapi.utils import get_openapi  # type: ignore
        except ModuleNotFoundError:
            print(
                "[dump_openapi] FastAPI is not installed in this Python.\n"
                "  Run: bash scripts/ensure_codegen_venv.sh\n"
                "  (That creates .tools/venv, installs deps, and runs this script.)",
                file=sys.stderr,
            )
            sys.exit(2)
        spec = get_openapi(title=title, version=version, routes=app.routes)
    # Ensure required keys (pin to 3.0.3 for widest tooling compatibility)
    spec["openapi"] = "3.0.3"
    spec.setdefault("info", {}).setdefault("title", title)
    spec["info"].setdefault("version", version)
    return _normalize(spec)

def _coerce_nullable_to_oas30(schema):
    """
    Convert Pydantic v2 / OpenAPI 3.1 style nullability (anyOf/oneOf with {"type": "null"}
    or type: ["X","null"]) into OpenAPI 3.0-style "nullable: true" schemas.
    Also handles $ref by wrapping in allOf because $ref cannot have sibling keys in OAS3.0.
    """
    if isinstance(schema, dict):
        # Recurse first
        for k in list(schema.keys()):
            schema[k] = _coerce_nullable_to_oas30(schema[k])
        # anyOf/oneOf → nullable
        for key in ("anyOf", "oneOf"):
            if key in schema and isinstance(schema[key], list):
                items = schema[key]
                null_found = any(isinstance(it, dict) and it.get("type") == "null" for it in items)
                non_null = next((it for it in items if not (isinstance(it, dict) and it.get("type") == "null")), None)
                if null_found and non_null is not None and len(items) == 2:
                    new_schema = {"nullable": True}
                    # Preserve helpful metadata that may live alongside the combiner
                    for meta_key in ("title","description","default","readOnly","writeOnly","deprecated","example","examples"):
                        if meta_key in schema:
                            new_schema[meta_key] = schema[meta_key]
                    if isinstance(non_null, dict) and set(non_null.keys()) == {"$ref"}:
                        # $ref cannot have siblings in OAS3.0 → wrap in allOf
                        new_schema["allOf"] = [{"$ref": non_null["$ref"]}]
                    elif isinstance(non_null, dict):
                        # Merge the non-null schema
                        for k, v in non_null.items():
                            new_schema[k] = v
                    else:
                        return schema
                    return new_schema
        # type: ["X","null"] → type: "X", nullable: true
        if isinstance(schema.get("type"), list):
            tlist = list(schema["type"])
            if "null" in tlist:
                non_null = [t for t in tlist if t != "null"]
                if len(non_null) == 1:
                    schema["type"] = non_null[0]
                    schema["nullable"] = True
        return schema
    elif isinstance(schema, list):
        return [_coerce_nullable_to_oas30(x) for x in schema]
    else:
        return schema

def write_spec(path: Path, spec: dict) -> None:
    # Validate before writing
    if not isinstance(spec, dict) or not isinstance(spec.get("openapi"), str) or not spec["openapi"].startswith("3."):
        raise RuntimeError(f"{path.name}: invalid OpenAPI object: missing or bad 'openapi' field")
    path.write_text(json.dumps(_normalize(spec), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {path}")

def main() -> None:
    # Import the apps *after* sys.path is set and after we've handled missing deps nicely.
    from gateway.app import app as gateway_app     # type: ignore
    from memory_api.app import app as memory_app   # type: ignore
    gw = _build_openapi(gateway_app, "BatVault Gateway", "0.1.0")
    mem = _build_openapi(memory_app, "BatVault Memory API", "0.1.0")
    write_spec(out_dir / "gateway.json", _coerce_nullable_to_oas30(gw))
    write_spec(out_dir / "memory.json", _coerce_nullable_to_oas30(mem))

if __name__ == "__main__":
    main()