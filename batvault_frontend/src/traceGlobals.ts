// Minimal globals for API + telemetry
export const GATEWAY_BASE   = import.meta.env.VITE_GATEWAY_BASE   || "";
export const MEMORY_BASE    = import.meta.env.VITE_MEMORY_BASE    || (window as any)?.BV_MEMORY_BASE || "";
export const QUERY_ENDPOINT = import.meta.env.VITE_QUERY_ENDPOINT || "/v2/query";
export const TOPIC_ENDPOINT = import.meta.env.VITE_TOPIC_ENDPOINT || "/v3/topic";

// ---- Canonical anchor format (source of truth for FE) ----
// "<domain>#<id>" where both sides are lowercase; id allows [a-z0-9._:-]
export const ANCHOR_RE = /^[a-z0-9]+#[a-z0-9._:-]+$/;
export function isAnchor(s: string | null | undefined): boolean {
  return ANCHOR_RE.test(String(s || "").trim());
}

// ---- Endpoint builders (base + path; path can be absolute) ----
export function queryEndpoint(): string {
  const base = (GATEWAY_BASE || "").trim();
  const path = (QUERY_ENDPOINT || "/v2/query").trim();
  if (/^https?:\/\//i.test(path)) return path;
  const origin = base || window.location.origin;
  return new URL(path, origin).toString();
}

export function topicEndpoint(): string {
  const base = (GATEWAY_BASE || "").trim();
  const path = (TOPIC_ENDPOINT || "/v3/topic").trim();
  if (/^https?:\/\//i.test(path)) return path;
  const origin = base || window.location.origin;
  return new URL(path, origin).toString();
}

// ---- Memory (enrich) base (FE -> Memory API) ----
// Priority: explicit VITE_MEMORY_BASE / BV_MEMORY_BASE -> derivation from gateway -> same-origin fallback.
export function memoryBase(): string {
  const explicit = (MEMORY_BASE || "").trim();
  if (explicit) return explicit.replace(/\/+$/, "");

  const gw = (GATEWAY_BASE || "").trim();
  if (gw) {
    try {
      const u = new URL(gw, window.location.origin);
      if (u.port) {
        const p = parseInt(u.port, 10);
        if (!Number.isNaN(p)) u.port = String(p + 2); // e.g. 8080 -> 8082
      }
      return u.toString().replace(/\/+$/, "");
    } catch {
      // fall through to same-origin path
    }
  }
  // Last-resort same-origin path (requires reverse-proxy to memory_api)
  return "/memory";
}

// Hook helpers may call this to publish trace identifiers to the window for debugging
export function publishTraceIds(ids: {
  request_id?: string;
  snapshot_etag?: string;
  policy_fp?: string;
  allowed_ids_fp?: string;
  graph_fp?: string;
  bundle_fp?: string;
}) {
  try {
    (window as any).setCurrentTrace?.(ids);
  } catch {
    // ignore
  }
}
