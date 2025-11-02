// Minimal globals for API + telemetry
import { ANCHOR_RE } from "./generated/grammar";
import { getRuntimeConfig } from "./config/runtime";

// === Observability session state (FE) =======================================
// Track the most recent trace ids so subsequent API calls (enrich, etc.) can
// reuse them for end-to-end correlation and UI display.
let __CURRENT_TRACE: { request_id?: string; snapshot_etag?: string } = {};

export function setCurrentTrace(ids: { request_id?: string; snapshot_etag?: string }) {
  __CURRENT_TRACE = { ...__CURRENT_TRACE, ...ids };
  try { (window as any).BV_CURRENT_REQUEST_ID = __CURRENT_TRACE.request_id; } catch { /* ignore */ }
}

export function currentRequestId(): string | undefined {
  return __CURRENT_TRACE.request_id || (typeof window !== "undefined" ? (window as any).BV_CURRENT_REQUEST_ID : undefined);
}

// UI-generated request IDs: UUID v4 (lowercase, canonical with dashes).
export function newRequestId(): string {
  // Prefer Web Crypto; fall back to Math.random() for SSR/non-secure contexts.
  const buf = new Uint8Array(16);
  if (typeof globalThis !== "undefined" && globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(buf);
  } else {
    for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256);
  }
  buf[6] = (buf[6] & 0x0f) | 0x40; // version 4
  buf[8] = (buf[8] & 0x3f) | 0x80; // variant 10
  const b = Array.from(buf).map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${b.slice(0, 8)}-${b.slice(8, 12)}-${b.slice(12, 16)}-${b.slice(16, 20)}-${b.slice(20)}`;
}

// ---- Canonical anchor format (generated from JSON Schema) ----
export function isAnchor(s: string | null | undefined): boolean {
  return ANCHOR_RE.test(String(s ?? "").trim());
}

// ---- Endpoint builders (base + path; path can be absolute) ----
function join(base: string, path: string): string {
  const b = (base ?? "").replace(/\/+$/, "");
  const p = path ?? "";
  // Absolute URL -> return as-is
  if (/^https?:\/\//i.test(p)) return p;
  // Empty path -> just the base
  if (!p) return b;
  // Root-relative path — prefer provided base; else same-origin (SSR-safe)
  if (p.startsWith("/")) {
    // If base host matches window.host but lacks a port while window has one,
    // prefer same-origin (preserves :5173 in local dev and reverse-proxy ports).
    try {
      const wOrigin = (typeof window !== "undefined" ? window.location.origin.replace(/\/+$/, "") : "");
      if (b && wOrigin) {
        const bu = new URL(b, wOrigin); // allow relative bases
        const wu = new URL(wOrigin);
        if (bu.hostname === wu.hostname && (!bu.port && wu.port)) {
          return `${wu.origin}${p}`;
        }
      }
    } catch { /* ignore */ }
    if (b) return `${b}${p}`;
    const origin = (typeof window !== "undefined" ? window.location.origin.replace(/\/+$/, "") : "");
    return origin ? `${origin}${p}` : p;
  }
  // Relative segment: prefer provided base, else window origin (SSR-safe)
  const origin = b || (typeof window !== "undefined" ? window.location.origin.replace(/\/+$/, "") : "");
  return origin ? `${origin}/${p}` : `/${p}`;
}

export function queryEndpoint(): string {
  const { gateway_base, endpoints } = getRuntimeConfig();
  return join(gateway_base, endpoints.query || "/v3/query");
}

// ---- Memory (enrich) base (FE -> Memory API) ----
export function memoryBase(): string {
  const { memory_base } = getRuntimeConfig();
  if (memory_base && memory_base.trim()) {
    return memory_base.replace(/\/+$/, "");
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
  stage_ms?: Record<string, number>;
}) {
  try {
    setCurrentTrace(ids);
    (window as any).setCurrentTrace?.(ids);
  } catch {
    // ignore
  }
}

// ---- Explicit helpers for Gateway base and bundle endpoints ----------------
export function gatewayBase(): string {
  const { gateway_base } = getRuntimeConfig();
  return (gateway_base || "").replace(/\/+$/, "");
}

export function bundlesPath(): string {
  const { endpoints } = getRuntimeConfig();
  return (endpoints?.bundles || "/v3/bundles").replace(/^\/?/, "/");
}

/** Convenience: full base for bundle routes, e.g. `${gatewayBase()}${bundlesPath()}` */
export function bundlesBase(): string {
  return `${gatewayBase()}${bundlesPath()}`;
}