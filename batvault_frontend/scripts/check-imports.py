#!/usr/bin/env python3
"""
Walk every *.ts/tsx file under ./src and verify that relative imports
(./, ../) actually point to a file that exists on disk – after TS-style
resolution (.ts, .tsx, .js, .jsx, index.ts, …).
"""
from pathlib import Path
import re, sys

IMPORT_RE = re.compile(r'^\s*import[\s\S]*?from\s+[\'"]([^\'"]+)[\'"]', re.M)

ALLOWED_EXT = (".ts", ".tsx", ".js", ".jsx")

def expand(base: Path, raw: str) -> list[Path]:
    """Return every file that could satisfy `raw`."""
    p = (base / raw).resolve()
    if p.suffix:                     # explicit extension
        return [p]
    cand = []
    for ext in ALLOWED_EXT:
        cand.append(p.with_suffix(ext))
    cand += [p / f"index{ext}" for ext in ALLOWED_EXT]
    return cand

errors: dict[str, list[str]] = {}
for src in Path("src").rglob("*.[tj]s*"):
    text = src.read_text(encoding="utf8", errors="ignore")
    for m in IMPORT_RE.finditer(text):
        path = m.group(1)
        # skip packages and @/* aliases
        if not (path.startswith(".") or path.startswith("..")):
            continue
        if any(c.exists() for c in expand(src.parent, path)):
            continue
        errors.setdefault(str(src), []).append(path)

if errors:
    for file, paths in errors.items():
        print(f"\n{file}:")
        for p in paths:
            print(f"   ✗  {p}")
    sys.exit(1)

print("✓  all relative imports resolve")
