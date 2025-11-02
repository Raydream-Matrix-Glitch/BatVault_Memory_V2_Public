// Typed clients + centralized headers for Gateway & Memory.
// No broad try/catch; explicit handling for invariants (412, policy_fp audit).
import createClient from "openapi-fetch";
import type { GatewayPaths } from "./types";
import { buildPolicyHeaders } from "../utils/policy";
import { idempotencyKey, idempotencyKeyFromSession } from "./utils/idempotency";

/** -------------------------------------------------------------------------
 * Adopt server policy_fp from any Response headers (single responsibility)
 * --------------------------------------------------------------------------*/
function adoptPolicyFromHeaders(h: Headers | null | undefined) {
  try {
    const p = h?.get?.("X-BV-Policy-Fingerprint");
    if (p && (window as any).BV_POLICY_KEY !== p) {
      (window as any).BV_POLICY_KEY = p;
    }
  } catch {
    /* ignore header/DOM access issues */
  }
}

/** -------------------------------------------------------------------------
 * Safely stringify heterogeneous error payloads without touching Response body
 * --------------------------------------------------------------------------*/
function stringifyError(err: unknown): string {
  try {
    if (err == null) return "";
    if (typeof err === "string") return err;
    if (typeof err === "object") {
      const anyErr = err as any;
      if (typeof anyErr.detail === "string") return anyErr.detail;
      return JSON.stringify(anyErr);
    }
    return String(err);
  } catch {
    return "";
  }
}

/** -------------------------------------------------------------------------
 * traceparent helpers
 * --------------------------------------------------------------------------*/
function hex(n: number) {
  const bytes = crypto.getRandomValues(new Uint8Array(n));
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += bytes[i].toString(16).padStart(2, "0");
  return s;
}
function makeTraceparent(traceId?: string): string {
  const tid = traceId && /^[0-9a-f]{32}$/i.test(traceId) ? traceId : hex(16);
  const pid = hex(8);
  return `00-${tid}-${pid}-01`;
}

/** -------------------------------------------------------------------------
 * Common headers: policy + tracing + json
 * --------------------------------------------------------------------------*/
export function commonHeaders(extra?: Record<string, string>, requestId?: string): Record<string, string> {
  const base = buildPolicyHeaders(requestId);
  const traceparent = makeTraceparent(base["X-Trace-Id"]);
  return {
    ...base,
    traceparent,
    "Content-Type": "application/json",
    Accept: "application/json",
    ...(extra || {}),
  };
}

/** -------------------------------------------------------------------------
 * Gateway typed client (used elsewhere in the app)
 * --------------------------------------------------------------------------*/
export const gateway = createClient<GatewayPaths>({ baseUrl: "" });

/** -------------------------------------------------------------------------
 * Lightweight UI breadcrumb sink (optional; used to prove proxying via Gateway)
 * --------------------------------------------------------------------------*/
function uiLog(event: string, payload?: Record<string, unknown>) {
  try {
    const body = JSON.stringify({ event, ...payload });
    if (typeof navigator !== "undefined" && typeof (navigator as any).sendBeacon === "function") {
      (navigator as any).sendBeacon("/v3/ui/logs", body);
    } else {
      fetch("/v3/ui/logs", { method: "POST", headers: { "content-type": "application/json" }, body }).catch(() => {});
    }
  } catch {
    /* non-fatal */
  }
}

/** -------------------------------------------------------------------------
 * POST /api/enrich/batch (proxied through Gateway at /memory/...)
 *  - Keeps Memory hidden (same-origin call to Gateway)
 *  - Avoids URL-encoding slashes by using a literal path (no {path} template)
 *  - Reads the body at most once per code path
 * --------------------------------------------------------------------------*/
export async function enrichBatch(params: {
  snapshotEtag: string;
  body: unknown;
  requestId?: string;
  reuseLastIdempotencyKey?: boolean;
}) {
  const { snapshotEtag, body, requestId, reuseLastIdempotencyKey } = params;

  const headers = commonHeaders({ "X-Snapshot-ETag": snapshotEtag }, requestId);
  headers["Idempotency-Key"] = reuseLastIdempotencyKey
    ? await idempotencyKeyFromSession(body, requestId)
    : await idempotencyKey(body);

  uiLog("fe.enrich_batch.call", {
    url: "/memory/api/enrich/batch",
    request_id: requestId,
    idempotency_key: headers["Idempotency-Key"],
    snapshot_etag: snapshotEtag,
  });

  const resp = await fetch("/memory/api/enrich/batch", {
    method: "POST",
    headers,
    body: JSON.stringify(body ?? {}),
  });

  uiLog("fe.enrich_batch.resp", { request_id: requestId, status: resp.status });

  // Mirror policy_fp etc. for subsequent calls (does not read body)
  adoptPolicyFromHeaders(resp.headers);

  if (resp.status === 412) {
    // Safe to read once here
    let detail = "";
    try {
      detail = await resp.text();
    } catch {
      /* ignore */
    }
    uiLog("fe.enrich_batch.412", { request_id: requestId, detail });
    throw new Error(`precondition_failed:${detail || "snapshot_etag_mismatch"}`);
  }

  if (!resp.ok) {
    // Prefer JSON when advertised; read exactly once
    let detail = "";
    const ctype = (resp.headers.get("content-type") || "").toLowerCase();
    if (ctype.includes("application/json")) {
      try {
        const j = await resp.json();
        detail = stringifyError(j);
      } catch {
        /* fall through to text */
      }
    }
    if (!detail) {
      try {
        detail = await resp.text();
      } catch {
        /* ignore */
      }
    }
    uiLog("fe.enrich_batch.error", { request_id: requestId, status: resp.status, detail });
    throw new Error(`enrich_failed:${resp.status}:${detail}`);
  }

  // Success → parse once and return
  const data = (await resp.json()) as unknown;
  uiLog("fe.enrich_batch.ok", { request_id: requestId });
  return data as any;
}
