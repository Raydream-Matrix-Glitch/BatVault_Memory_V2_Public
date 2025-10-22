import { useCallback, useRef, useState } from "react";
import { postStream } from "../utils/sse";
import { normalizeErrorMessage } from "../utils/errors";
import type { BundleView, GraphView } from "../types/memory";

interface StreamState<T> {
  isStreaming: boolean;
  error: string | null;
  events: any[];
  finalData: T | null;
}

export function useSSE<T = any>() {
  const [state, setState] = useState<StreamState<T>>({
    isStreaming: false,
    error: null,
    events: [],
    finalData: null,
  });
  const abortRef = useRef<AbortController | null>(null);

  const start = useCallback(async (endpoint: string, body: Record<string, unknown>, headers?: Record<string,string>) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState(s => ({ ...s, isStreaming: true, error: null, events: [], finalData: null }));
    try {
      await postStream({
        endpoint,
        body,
        headers,
        signal: ac.signal,
        onEvent: (ev) => {
          setState(s => ({ ...s, events: [...s.events, ev] }));
        },
        onDone: (finalObj) => {
          // Normalise: accept either the envelope ({schema_version,"response":{...}})
          // or the unwrapped WhyDecisionResponse (legacy streaming).
          const normalized = (finalObj && (finalObj as any).response) ? (finalObj as any).response : finalObj;
          setState(s => ({ ...s, finalData: normalized as T }));
        }
      });
    } catch (e: any) {
      setState(s => ({ ...s, error: normalizeErrorMessage(e?.message ?? String(e)) }));
    } finally {
      setState(s => ({ ...s, isStreaming: false }));
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setState(s => ({ ...s, isStreaming: false }));
  }, []);

  return { ...state, start, cancel };
}

// ---- Offline mock helpers (V3-shaped) ----

export function mockBundle(decisionRef = "mock-decision-001"): BundleView {
  const now = new Date();
  const iso = new Date(Math.floor(now.getTime()/1000)*1000).toISOString().replace(/\.\d{3}Z$/,'Z');
  const allowed = ["e1","e2","e3","d_next"];
  const edges: GraphView["graph"]["edges"] = [
    { type: "LED_TO", from: "e1", to: decisionRef, timestamp: iso, domain: "market", orientation: "preceding" },
    { type: "CAUSAL", from: decisionRef, to: "d_next", timestamp: iso, domain: "strategy", orientation: "succeeding" },
    { type: "ALIAS_OF", from: "ae1", to: decisionRef, timestamp: iso, domain: "product" }
  ];
  const memory: GraphView = {
    anchor: { id: decisionRef, kind: "DECISION", title: decisionRef, timestamp: iso },
    graph: { edges },
    meta: {
      allowed_ids: allowed,
      fingerprints: { graph_fp: "mock_graph_fp" },
      allowed_ids_fp: "mock_allowed_ids_fp",
      policy_fp: "mock_policy_fp",
      snapshot_etag: "mock_snapshot_etag",
      stage_ms: { resolver: 12, evidence: 18, selector: 9, budget: 3, prompt: 7, llm: 42, render: 6 },
      memory_spans_ms: { resolve_anchor: 5, k1_domain: 2, alias_inbound: 1, alias_tail: 1, dedupe_edges: 1, build_meta: 1 },
      alias: { partial: false, returned: ["ae1"], max_depth: 1 }
    }
  };
  return {
    answer: { short_answer: "Mock short answer.", cited_ids: ["e1","e2"] },
    memory,
    meta: {
      request_id: "mock_rid_123",
      bundle_fp: "mock_bundle_fp",
      budget_cfg_fp: "mock_budget_cfg_fp",
      policy_fp: memory.meta.policy_fp,
      allowed_ids_fp: memory.meta.allowed_ids_fp,
      graph_fp: memory.meta.fingerprints.graph_fp,
      snapshot_etag: memory.meta.snapshot_etag,
      stage_ms: memory.meta.stage_ms,
      memory_spans_ms: memory.meta.memory_spans_ms
    }
  };
}

export function mockTopicHits(q = "semis"): { query: string; hits: string[] } {
  return { query: q, hits: ["id_a","id_b","id_c"] };
}
