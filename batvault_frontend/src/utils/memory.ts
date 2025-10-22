import type { EnrichBatchResponse } from "../types/memory";
import { buildPolicyHeaders } from "./policy";
import { memoryBase } from "../traceGlobals";
import { logEvent } from "../utils/logger";

/** Canonicalize ID set (server-authoritative scope): normalize strings, sort, de-duplicate. */
export function canonicalizeAllowedIds(anchorId: string, ids: string[]): string[] {
  const seen = new Set<string>();
  return (ids || [])
    .filter((x) => typeof x === "string" && x.includes("#"))
    .map((x) => String(x))
    .sort()
    .filter((id) => (seen.has(id) ? false : (seen.add(id), true)));
}

/** POST /api/enrich/batch with proper policy headers and snapshot precondition. */
export async function enrichBatch(anchorId: string, snapshotEtag: string, ids: string[]): Promise<EnrichBatchResponse> {
  const base = memoryBase();
  const url = `${base}/api/enrich/batch`;
  const idsCanon = canonicalizeAllowedIds(anchorId, ids);
  const body = { anchor_id: anchorId, ids: idsCanon };
  try {
    logEvent("ui.enrich_batch.request", {
      base,
      ids_count: ids?.length ?? 0,
      has_snapshot: Boolean(snapshotEtag),
    });
  } catch { /* best-effort UI telemetry */ }

  // Optional: allow existence-minimizing personas to prefer 404 denials.
  // Source of truth: runtime flag via window or Vite env. Omit header unless "404".
  const deniedStatusRaw =
    String(((window as any)?.BV_DENIED_STATUS ?? (import.meta as any)?.env?.VITE_DENIED_STATUS ?? "")).trim();
  const deniedStatus = deniedStatusRaw === "404" ? "404" : "";
  if (deniedStatus === "404") {
    try { logEvent("ui.enrich_batch.denied_status", { denied_status: 404 }); } catch { /* ignore */ }
  }
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Snapshot-ETag": snapshotEtag || "",
      "If-Match": snapshotEtag || "",
      // Only send when explicitly requested; server default is 403.
      ...(deniedStatus ? { "X-Denied-Status": deniedStatus } : {}),
      ...buildPolicyHeaders(),
    },
    body: JSON.stringify(body),
  });
  if (res.status === 412) {
    // Upstream snapshot moved; FE must refresh the Exec Summary and retry (handled by caller).
    const detail = await safeText(res);
    throw new Error(`precondition_failed:${detail || "snapshot_etag_mismatch"}`);
  }
  if (!res.ok) {
    const detail = await safeText(res);
    throw new Error(`enrich_failed:${res.status}:${detail}`);
  }
  const json = (await res.json()) as EnrichBatchResponse;
  return json;
}

async function safeText(r: Response): Promise<string> {
  try { return await r.text(); } catch { return ""; }
}