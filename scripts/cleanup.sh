#!/usr/bin/env sh
# clean-caches.sh — remove Python bytecode, build artifacts, pip cache,
# optional apt caches (requires sudo/root), and Docker build cache.

set -eu
# Enable pipefail where supported (bash/zsh); ignore if not available.
set -o pipefail 2>/dev/null || true

echo "==> Starting cleanup from: $(pwd)"

# If we're inside a Git repo, operate from repo root for consistency.
if command -v git >/dev/null 2>&1; then
  if git rev-parse --show-toplevel >/dev/null 2>&1; then
    REPO_ROOT="$(git rev-parse --show-toplevel)"
    echo "==> Detected git repo. Changing to repo root: $REPO_ROOT"
    cd "$REPO_ROOT"
  fi
fi

echo "==> 1/5: Removing Python bytecode caches and editor backups..."
# __pycache__ directories
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
# Compiled Python files
find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null || true
# Editor backup files
find . -type f -name "*~" -delete 2>/dev/null || true

echo "==> 2/5: Removing Python build artifacts..."
rm -rf build/ dist/ .eggs 2>/dev/null || true
# Remove any *.egg-info directories anywhere in the tree
find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

echo "==> 3/5: Purging pip cache (if available)..."
if command -v pip >/dev/null 2>&1; then
  pip cache purge || true
fi
if command -v pip3 >/dev/null 2>&1; then
  pip3 cache purge || true
fi

echo "==> 4/5: Clearing apt caches (optional; requires root/sudo)..."
CLEAR_APT=0
if [ "$(id -u)" -eq 0 ]; then
  CLEAR_APT=1
elif command -v sudo >/dev/null 2>&1; then
  CLEAR_APT=2
fi

if [ "$CLEAR_APT" -eq 1 ]; then
  rm -rf /var/cache/apt/* /var/lib/apt/lists/* 2>/dev/null || true
elif [ "$CLEAR_APT" -eq 2 ]; then
  sudo rm -rf /var/cache/apt/* /var/lib/apt/lists/* 2>/dev/null || true
else
  echo "    (Skipping apt cache: not running as root and sudo not found.)"
fi

echo "==> 5/5: Pruning Docker build cache (if Docker is installed)..."
if command -v docker >/dev/null 2>&1; then
  docker builder prune --all --force || true
else
  echo "    (Skipping Docker prune: docker not found.)"
fi

echo "==> Cleanup complete."
