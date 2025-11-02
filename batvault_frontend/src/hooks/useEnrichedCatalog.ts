import { useCallback, useMemo, useRef, useState } from "react";
import { enrichBatch, canonicalizeAllowedIds } from "../utils/memory";
import { currentRequestId } from "../traceGlobals";
import type { EnrichedNode } from "../types/memory";
import { normalizeErrorMessage } from "../utils/errors";
import { logEvent } from "../utils/logger";

/**
 * Load Enriched Catalog for meta.allowed_ids. Caches by (snapshot_etag|allowed_ids_fp|policy_fp).
 */
export function useEnrichedCatalog() {
  const cacheRef = useRef<Map<string, { byId: Record<string, EnrichedNode>; ts: number }>>(new Map());
  const [state, setState] = useState<{ loading: boolean; error: string | null; itemsById: Record<string, EnrichedNode> | null }>({
    loading: false,
    error: null,
    itemsById: null,
  });

  const load = useCallback(async (params: {
    anchorId: string;
    snapshotEtag: string;
    allowedIds: string[];
    cacheKey: string; // usually `${snapshot}|${allowed_ids_fp}|${policy_fp}`
  }) => {
    const { anchorId, snapshotEtag, allowedIds, cacheKey } = params;
    const cached = cacheRef.current.get(cacheKey);
    if (cached) {
      setState({ loading: false, error: null, itemsById: cached.byId });
      return { fromCache: true, itemsById: cached.byId };
    }
    // Baseline v3: request *exactly* meta.allowed_ids; canonicalize only (sort/dedupe). No domain filtering; scope is server-authoritative.
    const ids = canonicalizeAllowedIds(anchorId, allowedIds);
    if (ids.length !== (allowedIds?.length ?? 0)) {
      try {
        logEvent("ui.allowed_ids.normalized", { before: allowedIds?.length ?? 0, after: ids.length });
      } catch {}
    }
    setState({ loading: true, error: null, itemsById: null });
    try {
      const res = await enrichBatch({
        snapshotEtag,
        body: { anchor_id: anchorId, ids },
        requestId: currentRequestId(),
        reuseLastIdempotencyKey: true
      });
      const byId = res.items || {};
      cacheRef.current.set(cacheKey, { byId, ts: Date.now() });
      setState({ loading: false, error: null, itemsById: byId });
      return { fromCache: false, itemsById: byId };
    } catch (e: any) {
      const msg = normalizeErrorMessage(e?.message ?? e);
      setState({ loading: false, error: msg, itemsById: null });
      throw e;
    }
  }, []);

  const clear = useCallback(() => {
    cacheRef.current.clear();
    setState({ loading: false, error: null, itemsById: null });
  }, []);

  return { ...state, load, clear };
}