from __future__ import annotations
import argparse
import ast
import sys
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

STD_NAMES: Set[str] = set(getattr(sys, "stdlib_module_names", ()))

IGNORED_DIRS = {"tests", "test", ".venv", "venv", ".git", "__pycache__", "scripts", "build", "dist"}
PY_EXT = (".py",)

ROOTS_TO_SCAN = ("packages", "services")

def read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)

def normalize_dist_name(name: str) -> str:
    # Strip extras and version pins, normalize -/_ and lowercase
    # e.g. "uvicorn[standard]>=0.29.0" -> "uvicorn"
    name = name.strip()
    name = name.split(";")[0]  # drop environment markers
    name = re.split(r"[<>=!~]", name, maxsplit=1)[0]
    name = re.sub(r"\[.*?\]$", "", name)
    return name.replace("_", "-").lower()

def load_project_pyproject(pyproj: Path) -> Tuple[str, List[str]]:
    data = read_toml(pyproj)
    proj = data.get("project", {})
    name = proj.get("name")
    if not name:
        raise ValueError(f"Missing [project].name in {pyproj}")
    deps = proj.get("dependencies", []) or []
    # Optionally include some optional deps groups if you want them to count as runtime
    # We purposely ignore typical dev/test groups.
    opt = proj.get("optional-dependencies", {}) or {}
    for group_name, group_deps in (opt.items() if isinstance(opt, dict) else []):
        if str(group_name).lower() in {"dev", "test", "tests", "lint", "docs"}:
            continue
        # If your project uses runtime extras, uncomment the next line to include them:
        # deps += group_deps
    return name, deps

def find_projects(root: Path) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for base in ROOTS_TO_SCAN:
        base_dir = root / base
        if not base_dir.exists():
            continue
        for pyproj in base_dir.rglob("pyproject.toml"):
            try:
                name, _ = load_project_pyproject(pyproj)
            except Exception:
                continue
            out[name] = pyproj.parent
    return out

def find_src_roots(project_dir: Path) -> List[Path]:
    # src/ preferred; fallback to project_dir if needed
    src = project_dir / "src"
    if src.exists():
        return [src]
    return [project_dir]

def top_level_packages_in_dir(src_root: Path) -> Set[str]:
    names: Set[str] = set()
    if not src_root.exists():
        return names
    for item in src_root.iterdir():
        if item.is_dir() and (item / "__init__.py").exists():
            names.add(item.name)
        elif item.is_file() and item.suffix == ".py":
            names.add(item.stem)
    return names

def collect_internal_module_map(projects: Dict[str, Path]) -> Dict[str, str]:
    # Map top-level import name -> project name
    mapping: Dict[str, str] = {}
    for proj_name, proj_dir in projects.items():
        for src_root in find_src_roots(proj_dir):
            for name in top_level_packages_in_dir(src_root):
                mapping[name] = proj_name
    return mapping

def iter_python_files(src_roots: List[Path]) -> Path:
    for src in src_roots:
        if not src.exists():
            continue
        for p in src.rglob("*.py"):
            # skip ignored dirs
            if any(seg in IGNORED_DIRS for seg in p.parts):
                continue
            yield p

def parse_imports(py_file: Path) -> Set[str]:
    code = py_file.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(code, filename=str(py_file))
    except SyntaxError:
        return set()
    mods: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top:
                    mods.add(alias.name)  # keep full path; we handle top-level later
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # relative import -> internal within same project; ignore for external mapping
                continue
            if node.module:
                mods.add(node.module)
    return mods

def load_depsmap(root: Path) -> Dict[str, str]:
    # Key can be "top" (e.g., "redis") or full path (e.g., "opentelemetry.instrumentation.fastapi")
    # Value must be the pip distribution name.
    path = root / "depsmap.toml"
    mapping: Dict[str, str] = {}
    if not path.exists():
        # Built-in minimal defaults; extend via depsmap.toml
        return {
            "prometheus_client": "prometheus-client",
            "opentelemetry": "opentelemetry-api",
            "opentelemetry.sdk": "opentelemetry-sdk",
            "opentelemetry.exporter.otlp.proto.http": "opentelemetry-exporter-otlp-proto-http",
            "opentelemetry.instrumentation.fastapi": "opentelemetry-instrumentation-fastapi",
            "opentelemetry.instrumentation.redis": "opentelemetry-instrumentation-redis",
            "pydantic_settings": "pydantic-settings",
            "python_dateutil": "python-dateutil",
            "sentence_transformers": "sentence-transformers",
            "argon2": "argon2-cffi",
            "orjson": "orjson",
            "fastapi": "fastapi",
            "httpx": "httpx",
            "redis": "redis",
            "minio": "minio",
            "uvicorn": "uvicorn",
            "numpy": "numpy",
            "jsonschema": "jsonschema",
            "six": "six",
            "arango": "python-arango",
            "Crypto": "pycryptodome",
            "slowapi": "slowapi",
        }
    data = read_toml(path)
    table = data.get("module_to_dist", {}) or {}
    for k, v in table.items():
        mapping[str(k)] = str(v)
    return mapping

def map_external_module_to_dist(mod: str, alias_map: Dict[str, str]) -> Optional[str]:
    # Try exact match (full path), then gradually trim to top-level.
    parts = mod.split(".")
    for i in range(len(parts), 0, -1):
        key = ".".join(parts[:i])
        if key in alias_map:
            return normalize_dist_name(alias_map[key])
    # Heuristic: top-level import often equals dist name.
    top = parts[0]
    if top not in STD_NAMES:
        return normalize_dist_name(top)
    return None

def declared_deps_sets(pyproj: Path) -> Tuple[Set[str], Set[str]]:
    name, deps = load_project_pyproject(pyproj)
    internal: Set[str] = set()
    external: Set[str] = set()
    # internal deps are other project names present in the repo
    # we can't know them yet here; caller will translate by intersecting.
    # For now, return external only; internal handled separately.
    for d in deps:
        norm = normalize_dist_name(d)
        external.add(norm)
    return internal, external

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("."), help="Repository root")
    ap.add_argument("--strict", action="store_true", help="Fail on unknown external modules (no alias mapping)")
    args = ap.parse_args()

    root = args.root.resolve()
    projects = find_projects(root)
    if not projects:
        print("No projects found.", file=sys.stderr)
        return 0

    # Build internal module -> project name map
    internal_mod_to_proj = collect_internal_module_map(projects)
    alias_map = load_depsmap(root)

    any_errors = False
    report = {}

    for proj_name, proj_dir in projects.items():
        pyproj = proj_dir / "pyproject.toml"
        _, declared_external = declared_deps_sets(pyproj)
        declared_external = {normalize_dist_name(d) for d in declared_external}

        # figure declared internal by intersecting declared names with project names
        data = read_toml(pyproj)
        declared_names = {normalize_dist_name(d) for d in data.get("project", {}).get("dependencies", []) or []}
        declared_internal = {pname for pname in projects.keys() if normalize_dist_name(pname) in declared_names}

        needed_internal: Set[str] = set()
        needed_external: Set[str] = set()
        unknown_external_modules: Set[str] = set()

        src_roots = find_src_roots(proj_dir)
        seen_modules: Set[str] = set()
        for f in iter_python_files(src_roots):
            for mod in parse_imports(f):
                if not mod:
                    continue
                top = mod.split(".", 1)[0]
                if top in STD_NAMES:
                    continue
                # internal?
                if top in internal_mod_to_proj:
                    target_proj = internal_mod_to_proj[top]
                    if target_proj != proj_name:
                        needed_internal.add(target_proj)
                    continue
                # external
                dist = map_external_module_to_dist(mod, alias_map)
                if dist:
                    needed_external.add(dist)
                else:
                    unknown_external_modules.add(mod)
                seen_modules.add(mod)

        missing_internal = sorted(needed_internal - declared_internal)
        missing_external = sorted({normalize_dist_name(x) for x in needed_external} - declared_external)

        if missing_internal or missing_external or (args.strict and unknown_external_modules):
            any_errors = True

        report[proj_name] = {
            "path": str(proj_dir.relative_to(root)),
            "missing_internal": missing_internal,
            "missing_external": missing_external,
            "unknown_external_modules": sorted(unknown_external_modules),
        }

    # Pretty print actionable output
    for proj_name, info in report.items():
        mi = info["missing_internal"]
        me = info["missing_external"]
        u = info["unknown_external_modules"]
        if mi or me or (args.strict and u):
            print(f"\n[{proj_name}] -> {info['path']}")
            if mi:
                print("  Missing INTERNAL deps (other packages in this repo):")
                for x in mi:
                    print(f"    - {x}")
            if me:
                print("  Missing EXTERNAL deps (pip distributions):")
                for x in me:
                    print(f"    - {x}")
            if u:
                status = "Unknown external modules (add to depsmap.toml under [module_to_dist] or declare explicitly):"
                print("  " + status)
                for x in u:
                    print(f"    - {x}")
            # Suggest a TOML snippet
            if mi or me:
                print("  Suggested additions to [project.dependencies]:")
                for x in mi:
                    print(f'    "{"%s" % x}",')
                for x in me:
                    print(f'    "{"%s" % x}",')

    if any_errors:
        print("\nDependency check FAILED. See missing deps above.", file=sys.stderr)
        return 1
    else:
        print("Dependency check passed.")
        return 0

if __name__ == "__main__":
    raise SystemExit(main())


