import type { EnrichBatchResponse } from "../types/memory";
import { enrichBatch as sdkEnrichBatch } from "../sdk/client";

/** Canonicalize ID set (server-authoritative scope): normalize strings, sort, de-duplicate. */
export function canonicalizeAllowedIds(_anchorId: string, ids: string[]): string[] {
  const seen = new Set<string>();
  return (ids || [])
    .filter((x) => typeof x === "string" && x.includes("#"))
    .map((x) => String(x))
    .sort()
    .filter((id) => (seen.has(id) ? false : (seen.add(id), true)));
}

/** POST /api/enrich/batch (snapshot-bound). Uses the generated SDK. */
export async function enrichBatch(params: {
  snapshotEtag: string;
  body: any;
  requestId?: string;
  reuseLastIdempotencyKey?: boolean;
}): Promise<EnrichBatchResponse> {
  const out = await sdkEnrichBatch({
    ...params,
    requestId: params.requestId || "",
    reuseLastIdempotencyKey: params.reuseLastIdempotencyKey
  });
  return out as unknown as EnrichBatchResponse;
}