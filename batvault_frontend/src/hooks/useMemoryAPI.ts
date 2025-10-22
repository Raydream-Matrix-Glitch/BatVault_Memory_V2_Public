import { useCallback, useEffect, useMemo, useRef } from "react";
import { useSSE } from "./useSSE";
import { publishTraceIds, queryEndpoint, isAnchor } from "../traceGlobals";
import { buildPolicyHeaders } from "../utils/policy";

/** Heuristic: extract a computed fingerprint from an error string (JSON payload embedded). */
function parsePolicyMismatchFingerprint(errText: string): string | null {
  if (!errText) return null;
  // Try to locate a JSON object in the string
  const idx = errText.indexOf("{");
  if (idx >= 0) {
    const tail = errText.slice(idx);
    try {
      const obj = JSON.parse(tail);
      // common shapes: { error:"policy_key_mismatch", computed:"sha256:..." } or with meta.computed
      const code = String(obj.error || obj.code || obj.detail || "").toLowerCase();
      if (code.includes("policy_key_mismatch")) {
        return obj.computed || obj?.meta?.computed || obj?.policy?.computed || null;
      }
    } catch {
      // ignore JSON parse errors; fall through to regex
    }
  }
  // Fallback regex: look for sha256:... pattern
  const m = /sha256:[0-9a-f]{64}/i.exec(errText);
  return m ? m[0] : null;
}

/** Convert streamed event payloads into displayable tokens (strings). */
function toToken(ev: any): string {
  if (typeof ev === "string") return ev;
  if (typeof ev?.token === "string") return ev.token;
  if (typeof ev?.delta === "string") return ev.delta;
  if (Array.isArray(ev?.tokens)) return ev.tokens.join("");
  try {
    return JSON.stringify(ev);
  } catch {
    return String(ev);
  }
}

export function useMemoryAPI() {
  const { start, cancel, events, isStreaming, error, finalData } = useSSE<any>();

  // Keep the last request so we can retry once if the backend hands us a new fingerprint.
  const lastReqRef = useRef<{ endpoint: string; body: Record<string, unknown> } | null>(null);
  const retriedForReqId = useRef<string | null>(null);

  const tokens = useMemo(() => (events || []).map(toToken), [events]);

  const queryDecision = useCallback(async (input: string) => {
    const endpoint = queryEndpoint();
    const text = (input || "").trim();
    // Anchored vs. unanchored is decided by the canonical schema.
    const body = isAnchor(text)
      ? { question: "Why this decision?", anchor: text }
      : { question: text };
    lastReqRef.current = { endpoint, body };
    retriedForReqId.current = null; // reset retry guard for this request
    await start(endpoint, body, buildPolicyHeaders());
  }, [start]);

  // Publish trace IDs for the drawer/debugger when the final bundle arrives
  useEffect(() => {
    if (!finalData?.meta) return;
    publishTraceIds({
      request_id: finalData?.meta?.request_id,
      snapshot_etag: finalData?.meta?.snapshot_etag,
      policy_fp: finalData?.meta?.policy_fp,
      allowed_ids_fp: finalData?.meta?.allowed_ids_fp,
      graph_fp: finalData?.meta?.fingerprints?.graph_fp,
      bundle_fp: finalData?.meta?.bundle_fp,
    });
  }, [finalData]);

  // Auto-adopt the backend's policy fingerprint exactly once per request ID.
  useEffect(() => {
    if (!error) return;
    const last = lastReqRef.current;
    if (!last) return;
    const retryKey = JSON.stringify(last);
    if (retriedForReqId.current === retryKey) return;

    const fp = parsePolicyMismatchFingerprint(String(error));
    if (fp) {
      (window as any).BV_POLICY_KEY = fp; // cache at runtime only
      retriedForReqId.current = retryKey;
      // retry quickly with the learned fingerprint
      start(last.endpoint, last.body, buildPolicyHeaders());
    }
  }, [error, start]);

  // Publish trace IDs for the drawers once final bundle arrives.
  useEffect(() => {
    if (!finalData?.meta) return;
    publishTraceIds({
      request_id: finalData?.meta?.request_id,
      snapshot_etag: finalData?.meta?.snapshot_etag,
      policy_fp: finalData?.meta?.policy_fp,
      allowed_ids_fp: finalData?.meta?.allowed_ids_fp,
      graph_fp: finalData?.meta?.fingerprints?.graph_fp,
      bundle_fp: finalData?.meta?.bundle_fp,
    });
  }, [finalData]);

  // Reserved for later enrichment path; returned to keep API stable.
  const topic: { query: string; hits: string[] } | null = null;

  return {
    queryDecision,
    isStreaming,
    error,
    finalData,
    cancel,
    tokens,
    topic,
  };
}
