#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-.}"

echo "[clean] Taking ownership of repo (needed to remove cache dirs)…"
sudo chown -R "$(id -u)":"$(id -g)" "$ROOT"

echo "[clean] Clearing immutable flags if present (safe to ignore errors)…"
sudo find "$ROOT" \( -type d -name "__pycache__" -o -type f -name "*.pyc" \) -exec chattr -f -i {} + 2>/dev/null || true
sudo chflags -R nouchg "$ROOT" 2>/dev/null || true

echo "[clean] Making files/dirs writable…"
find "$ROOT" -type d -exec chmod u+rwx {} +
find "$ROOT" -type f -exec chmod u+rw  {} +

echo "[clean] Deleting Python bytecode and caches…"
find "$ROOT" -type f -name "*.pyc" -delete
find "$ROOT" -type d -name "__pycache__" -prune -exec rm -rf -- {} +

echo "[clean] Done."
