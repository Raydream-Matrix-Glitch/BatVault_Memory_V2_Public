#!/usr/bin/env python3
"""
Policy smoke test:
 - injects a minimal policy passport (dev defaults) unless provided
 - hits /v2/ask with different policy headers
 - asserts policy_trace is present in meta + bundle
 - asserts downloads manifest/bundle contains _meta.json
 - compares event counts under stricter ceilings/roles

ENV:
  GATEWAY_URL (default: http://localhost:8080)
  ANCHOR_ID   (required) e.g. a known decision id from fixtures
  AUTH_TOKEN  (optional) e.g. "Bearer xyz"
"""
from __future__ import annotations
import json, os, sys, urllib.request, urllib.error, tarfile, io, time, uuid, hashlib

GW  = os.getenv("GATEWAY_URL", "http://localhost:8080")
ANCHOR_ID = os.getenv("ANCHOR_ID")
AUTH = os.getenv("AUTH_TOKEN")

def _uuid():
    return uuid.uuid4().hex

def _hdrs(extra: dict[str, str] | None = None) -> dict[str, str]:
    """
    Build headers with a minimal policy passport so Memory-API doesn't fail-close.
    Callers can still override any of these via `extra`.
    """
    h = {
        "Content-Type": "application/json",
        "User-Agent": "policy-smoke/1.0",
        # Dev-safe defaults (override in `extra` if needed)
        "X-User-Id": os.getenv("SMOKE_USER_ID", "u-smoke"),
        "X-Policy-Version": os.getenv("SMOKE_POLICY_VERSION", "v1"),
        # Policy key is used for cache partitioning/audit only in this demo.
        "X-Policy-Key": os.getenv("SMOKE_POLICY_KEY", "dev-smoke-key"),
        "X-Request-Id": _uuid(),
        "X-Trace-Id": _uuid(),
    }
    if AUTH:
        h["Authorization"] = AUTH
    if extra:
        h.update({k: v for k, v in extra.items() if v is not None})
    return h

def post_json(url: str, data: dict, headers: dict) -> tuple[int, dict, dict]:
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), json.loads(r.read().decode()), dict(r.getheaders())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code} {url}: {body}") from e

def get_bytes(url: str, headers: dict) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), r.read(), dict(r.getheaders())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code} {url}: {body}") from e

def ask(anchor_id: str, policy_headers: dict[str, str]) -> dict:
    url = f"{GW}/v2/ask?fresh=1"
    payload = {"intent": "why_decision", "anchor_id": anchor_id}
    code, data, _ = post_json(url, payload, _hdrs(policy_headers))
    if code != 200:
        raise RuntimeError(f"unexpected status {code}")
    return data

def fetch_bundle(bundle_url: str, policy_headers: dict[str, str]) -> dict:
    code, blob, _ = get_bytes(bundle_url, _hdrs(policy_headers))
    if code != 200:
        raise RuntimeError(f"bundle status {code}")
    tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:*")
    names = tf.getnames()
    if "_meta.json" not in names:
        raise AssertionError("bundle missing _meta.json")
    meta_member = tf.extractfile("_meta.json")
    meta = json.loads(meta_member.read().decode()) if meta_member else {}
    return {"names": names, "meta": meta}

def artifacts_from_meta(meta: dict) -> list[dict]:
    return ((meta or {}).get("downloads") or {}).get("artifacts") or []

def event_count(resp: dict) -> int:
    ev = (resp or {}).get("evidence") or {}
    events = ev.get("events") or []
    return len(events)

def ensure_policy_trace(meta: dict, where: str):
    pt = (meta or {}).get("policy_trace")
    if not isinstance(pt, dict):
        raise AssertionError(f"{where}: policy_trace missing or not a dict")

def main() -> int:
    if not ANCHOR_ID:
        print("ANCHOR_ID is required (export ANCHOR_ID=...)", file=sys.stderr)
        return 2

    # Test matrix (feel free to adapt to your roles/namespaces)
    cases = [
        # Use a valid namespace from the role profiles (per target.md); "internal" is safe for the demo.
        ("manager_high", {"X-User-Roles": "manager", "X-User-Namespaces": "internal", "X-Sensitivity-Ceiling": "high"}),
        ("viewer_high",  {"X-User-Roles": "viewer",  "X-User-Namespaces": "internal", "X-Sensitivity-Ceiling": "high"}),
        ("manager_low",  {"X-User-Roles": "manager", "X-User-Namespaces": "internal", "X-Sensitivity-Ceiling": "low"}),
    ]
    results: dict[str, dict] = {}

    for name, headers in cases:
        print(f"↪ running {name} …")
        resp = ask(ANCHOR_ID, headers)
        meta = resp.get("meta") or {}
        ensure_policy_trace(meta, f"resp.meta ({name})")
        arts = artifacts_from_meta(meta)
        bv = next((a for a in arts if a.get("name")=="bundle_view" and a.get("allowed") is True), None)
        bundle_url = resp.get("bundle_url") or (bv and bv.get("href"))
        if not bundle_url:
            raise AssertionError(f"{name}: missing bundle_url")
        bundle = fetch_bundle(bundle_url, headers)
        ensure_policy_trace(bundle.get("meta") or {}, f"bundle._meta ({name})")
        cnt = event_count(resp)
        results[name] = {"count": cnt, "bundle_names": bundle["names"]}
        print(f"  ✓ events={cnt}, bundle files: {len(bundle['names'])}")
        time.sleep(0.1)

    # Relative assertions (soft — depend on fixtures)
    c_mgr_high = results["manager_high"]["count"]
    c_mgr_low  = results["manager_low"]["count"]
    if c_mgr_low > c_mgr_high:
        raise AssertionError(f"expected manager_low (ceiling=low) <= manager_high; got {c_mgr_low} > {c_mgr_high}")

    print("\nAll checks passed.")
    for k, v in results.items():
        print(f"  - {k}: events={v['count']} files={len(v['bundle_names'])}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())


