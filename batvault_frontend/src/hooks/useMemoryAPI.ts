import { useCallback, useMemo, useEffect } from "react";
import { useQueryDecision } from "../sdk/react/useQueryDecision";
import { isAnchor } from "../traceGlobals";

/** Convert streamed event payloads into displayable tokens (strings). */
function toToken(ev: any): string {
  if (typeof ev === "string") return ev;
  if (typeof ev?.token === "string") return ev.token;
  if (typeof ev?.delta === "string") return ev.delta;
  if (Array.isArray(ev?.tokens)) return ev.tokens.join("");
  try { return JSON.stringify(ev); } catch { return String(ev); }
}

export function useMemoryAPI() {
  // Centralized streaming (idempotency, snapshot preconditions, policy-mismatch retry)
  const { state, queryDecision: _query, abort } = useQueryDecision({
    reconnectOnPolicyMismatch: true,
  });

  const tokens = useMemo(() => (state.tokens || []).map(toToken), [state.tokens]);

  // v3: Gateway may wrap the payload as { response: {...}, schema_version: "v3" }
  // Older builds returned fields at top-level. Normalize here so all UI reads the same shape.
  const finalNormalized = useMemo(() => {
    const f: any = state.final;
    if (!f) return null;
    const base = (f.response && typeof f.response === "object")
      ? f.response
      : (f.bundle && f.bundle.response)
        ? f.bundle.response // bundle verify view
        : f;

    // Merge fingerprint/correlation headers into meta iff missing in body
    try {
      const headers: any = (state as any)?.headers;
      const getHeader = (k: string): string | undefined => {
        if (!headers) return undefined;
        // Support both Fetch Headers and plain objects
        if (typeof headers.get === "function") {
          return headers.get(k) || headers.get(k.toLowerCase()) || undefined;
        }
        const low = k.toLowerCase();
        for (const [hk, hv] of Object.entries(headers)) {
          if (String(hk).toLowerCase() === low) return String(hv);
        }
        return undefined;
      };

      const meta = { ...(base?.meta || {}) };
      const candidates: Record<string, string | undefined> = {
        request_id:       getHeader("x-request-id"),
        snapshot_etag:    getHeader("x-snapshot-etag") || getHeader("x-response-snapshot-etag"),
        policy_fp:        getHeader("x-bv-policy-fingerprint"),
        allowed_ids_fp:   getHeader("x-bv-allowed-ids-fp"),
        graph_fp:         getHeader("x-bv-graph-fp"),
        schema_fp:        getHeader("x-bv-schema-fp"),
        bundle_fp:        getHeader("x-bv-bundle-fp"),
      };
      for (const [k, v] of Object.entries(candidates)) {
        if (v && (meta[k] === undefined || meta[k] === null || meta[k] === "")) {
          (meta as any)[k] = v;
        }
      }
      return { ...base, meta };
    } catch {
      return base;
    }
  }, [state.final]);

  // Adopt server policy_fp from the final JSON (headers are already adopted in useQueryDecision)
  useEffect(() => {
    try {
      const meta = (finalNormalized as any)?.meta || null;
      const pfp = meta?.policy_fp;
      if (pfp && (window as any).BV_POLICY_KEY !== pfp) {
        (window as any).BV_POLICY_KEY = pfp;
      }
    } catch { /* ignore */ }
  }, [finalNormalized]);

  // Also adopt server policy_fp from the final payload (works for non-stream JSON as well)
  useEffect(() => {
    try {
      const meta = (state?.final as any)?.meta || (state?.final as any)?.response?.meta || null;
      const pfp = meta?.policy_fp;
      if (pfp && (window as any).BV_POLICY_KEY !== pfp) {
        (window as any).BV_POLICY_KEY = pfp;
      }
    } catch { /* ignore */ }
  }, [state?.final]);

  const queryDecision = useCallback(
    async (input: string) => {
      const text = (input || "").trim();
      const body = isAnchor(text)
        ? { question: "Why this decision?", anchor: text }
        : { question: text };
      await _query(body);
    },
    [_query]
  );

  const finalPayload = (state?.final as any)?.response ?? (state?.final as any);
  return {
    queryDecision,
    isStreaming: state.isStreaming,
    error: state.error,
    finalData: finalNormalized as any,
    cancel: abort,
    tokens,
  };
}
