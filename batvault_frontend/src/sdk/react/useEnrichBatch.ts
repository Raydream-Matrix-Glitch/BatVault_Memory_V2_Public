/**
 * sdk/react/useEnrichBatch.ts
 * Typed hook for POST /api/enrich/batch with idempotency and snapshot preconditions.
 */
import { useCallback, useMemo, useState } from "react";
import { enrichBatch as apiEnrichBatch } from "../client";
import { newRequestId } from "../../traceGlobals";
import type { EnrichBatchResponse } from "../../types/memory";

export function useEnrichBatch(snapshotEtag: string) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [data, setData] = useState<EnrichBatchResponse | null>(null);
  const requestId = useMemo(() => newRequestId(), []);

  const run = useCallback(async (body: any, reuseLastIdempotencyKey = false) => {
    setLoading(true); setError(null);
    try {
      const res = await apiEnrichBatch({
        snapshotEtag,
        body,
        requestId,
        reuseLastIdempotencyKey
      });
      setData(res);
    } catch (e:any) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [snapshotEtag, requestId]);

  return { run, loading, error, data, requestId };
}