from __future__ import annotations
from hashlib import blake2s

def _s(x: object | None) -> str:
    return "" if x is None else str(x)

def _fp(*parts: object) -> str:
    """
    Stable, compact fingerprint for values we don't want to embed verbatim
    into Redis keys (keeps keys short and privacy-friendly).
    """
    h = blake2s(digest_size=10)
    for p in parts:
        h.update(_s(p).encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()

# ------------------------------
# Gateway keys (hard namespaced)
# ------------------------------
_NS_GW = "bv:gw:v1"

def gw_evidence(snapshot_etag: str | None,
                allowed_ids_fp: str | None,
                policy_fp: str | None) -> str:
    """Gateway evidence cache key (namespaced)."""
    return f"{_NS_GW}:evidence:{_s(snapshot_etag)}|{_s(allowed_ids_fp)}|{_s(policy_fp)}"

def evidence(snapshot_etag: str | None,
             allowed_ids_fp: str | None,
             policy_fp: str | None) -> str:
    """
    Gateway evidence key (hard-namespaced).
    Former legacy shape `evidence:{...}` is removed.
    """
    return gw_evidence(snapshot_etag, allowed_ids_fp, policy_fp)

def gw_bundle(bundle_fp: str | None) -> str:
    """Gateway bundle cache key (namespaced)."""
    return f"{_NS_GW}:bundle:{_s(bundle_fp)}"

def bundle(bundle_fp: str | None) -> str:
    """
    Gateway bundle key (hard-namespaced).
    Former legacy shape `bundle:{...}` is removed.
    """
    return gw_bundle(bundle_fp)

# ------------------------------
# Memory API keys (new, namespaced)
# ------------------------------
_NS_MEM = "bv:mem:v1"

def mem_resolve(snapshot_etag: str | None,
                policy_fp: str | None,
                query_str: str | None) -> str:
    """
    Memory resolve cache key (for /api/resolve/text).
    Namespaced to prevent any collision with Gateway caches.
    """
    return f"{_NS_MEM}:resolve:{_fp(snapshot_etag, policy_fp, query_str)}"

def mem_expand_candidates(snapshot_etag: str | None,
                          policy_fp: str | None,
                          anchor_id: str | None) -> str:
    """
    Memory expand cache key (for /api/graph/expand_candidates) — only if/when enabled.
    """
    return f"{_NS_MEM}:expand:{_fp(snapshot_etag, policy_fp, anchor_id)}"

def mem_masked(snapshot_etag: str | None,
               policy_fp: str | None,
               anchor_id: str | None,
               alias_on: int | None,
               alias_hops: int | None) -> str:
    """
    Memory masked-view cache key (if used).
    """
    return f"{_NS_MEM}:masked:{_fp(snapshot_etag, policy_fp, anchor_id, alias_on, alias_hops)}"