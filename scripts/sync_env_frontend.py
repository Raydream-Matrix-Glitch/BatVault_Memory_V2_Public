#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
env_path = ROOT / ".env"
fe_env_path = ROOT / "batvault_frontend" / ".env"

MAPPING = {
    "MEMORY_API_URL": "VITE_MEMORY_BASE",
    "POLICY_KEY": "VITE_POLICY_KEY",
    "GATEWAY_BASE": "VITE_GATEWAY_BASE",
}

def parse_env(path: Path) -> dict[str,str]:
    out: dict[str,str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "=" not in line: continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def main() -> int:
    if not env_path.exists():
        print(f"error: {env_path} not found", file=sys.stderr)
        return 1
    env = parse_env(env_path)
    lines = []
    for src, dst in MAPPING.items():
        val = env.get(src, "")
        if val:
            lines.append(f"{dst}={val}")
    fe_env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {fe_env_path.relative_to(ROOT)} with {len(lines)} vars.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
