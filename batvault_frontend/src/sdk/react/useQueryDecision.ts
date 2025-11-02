import { useMemo, useCallback } from "react";
import { useSSE } from "./useSSE";
import { idempotencyKey, idempotencyKeyFromSession } from "../utils/idempotency";
import { newRequestId, queryEndpoint, publishTraceIds } from "../../traceGlobals";
import type { MemoryMetaV3SnapshotBound } from "../../types/generated/memory.meta";

export interface QueryEnvelope {
  question: string;
  anchor?: string;
  allowed_ids?: string[];
  k?: number;
  explain?: boolean;
}

export type QueryFinal = {
  answer?: unknown;
  meta?: { memory?: MemoryMetaV3SnapshotBound; request_id?: string; snapshot_etag?: string; policy_fp?: string; allowed_ids_fp?: string; fingerprints?: { graph_fp?: string }; bundle_fp?: string };
};

export function useQueryDecision(opts: {
  snapshotEtag?: string;
  reconnectOnPolicyMismatch?: boolean;
}) {
  const requestId = useMemo(() => newRequestId(), []);
  const sse = useSSE<QueryFinal>({
    endpoint: queryEndpoint(),
    snapshotEtag: opts.snapshotEtag,
    reconnectOnPolicyMismatch: Boolean(opts.reconnectOnPolicyMismatch),
    requestId
  });

  // Keep API parity with legacy hook
  const queryDecision = useCallback(async (body: QueryEnvelope, reuseLastIdempotencyKey = false) => {
    const key = reuseLastIdempotencyKey
      ? (idempotencyKeyFromSession() || await idempotencyKey(body))
      : await idempotencyKey(body);
    const extraHeaders = { "Idempotency-Key": String(key) };
    await sse.start(body, extraHeaders);
    const h = sse.state.headers;
    if (h) {
      publishTraceIds({
        request_id: (h.get("x-request-id") || requestId)!,
        snapshot_etag: h.get("x-snapshot-etag") || undefined,
        policy_fp: h.get("x-bv-policy-fingerprint") || undefined,
        allowed_ids_fp: h.get("x-bv-allowed-ids-fp") || undefined,
        graph_fp: h.get("x-bv-graph-fp") || undefined,
        bundle_fp: h.get("x-bv-bundle-fp") || undefined,
      });
    }
  }, [requestId, sse]);

  const fromReport = useCallback((tokens: any[], final?: QueryFinal) => {
    sse.fromReport(tokens as any, final);
  }, [sse]);

  return { queryDecision, fromReport, abort: sse.abort, state: sse.state, requestId };
}