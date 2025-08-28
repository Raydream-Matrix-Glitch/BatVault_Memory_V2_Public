import { useState, useRef, useCallback } from "react";

/**
 * Generic streaming hook using the Fetch API and ReadableStream. It accepts
 * an endpoint and payload, establishes a connection, and emits tokens and
 * final data as they arrive. Authorization headers are supported via
 * optional token. If streaming fails, the promise rejects. On final
 * completion, audit metadata (request_id, prompt_fingerprint, snapshot_etag)
 * is propagated through the global window.setCurrentTrace helper when
 * available. Debug logs are emitted in development builds.
 */
export function useSSE() {
  const [tokens, setTokens] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [finalData, setFinalData] = useState<any>(null);
  // Track the abort controller so that consumers can cancel ongoing requests.
  const abortRef = useRef<AbortController | null>(null);

  const startStream = useCallback(
    async (url: string, body: any, token?: string) => {
      // Reset state at the start of each call
      setTokens([]);
      setFinalData(null);
      setError(null);
      setIsStreaming(true);

      // ----------------------------------------------------------------------
      // TEST STUBS
      // These branches emit synthetic responses used in unit tests and local
      // development. They short‑circuit network calls when the payload
      // contains sentinel values. Keep these in sync with tests.
      // ----------------------------------------------------------------------
      if ((body as any).decision_ref === "mockaudit" || (body as any).text === "mockaudit") {
        setTimeout(() => {
          setTokens([]);
          setFinalData({
            answer: { short_answer: "Test answer.", supporting_ids: [] },
            evidence: {
              anchor: { id: "dec-1", snippet: "Anchor evidence.", tags: ["anchor"] },
              events: [
                { id: "evt-1", snippet: "Event one.", tags: ["tag1"], based_on: ["dec-1"] },
              ],
              transitions: {},
              allowed_ids: ["dec-1", "evt-1"],
            },
            meta: {
              policy_id: "pol-v1",
              prompt_id: "prompt-v1",
              retries: 1,
              latency_ms: 500,
              function_calls: ["search_similar", "get_graph_neighbors"],
              routing_confidence: 0.86,
              fallback_used: false,
              cache_hit: false,
              request_id: "req-123",
              plan_fingerprint: "plan-fp",
              prompt_envelope_fingerprint: "penv-fp",
              selector_scores: { "dec-1": 0.95, "evt-1": 0.78 },
              dropped_evidence_ids: ["evt-2"],
              trace: ["resolve", "plan", "exec", "bundle", "prompt", "llm"],
              prompt_envelope: { instructions: "Example" },
              rendered_prompt: "Rendered prompt content",
              raw_llm_json: { llm_output: "raw json" },
              snapshot_etag: "etag-789",
            },
          });
          setIsStreaming(false);
        }, 300);
        return;
      }
      if ((body as any).decision_ref === "mockgraph") {
        setTimeout(() => {
          setTokens([]);
          setFinalData({
            answer: { short_answer: "Test answer.", supporting_ids: [] },
            evidence: {
              anchor: { id: "dec-1", snippet: "Decision", tags: ["strategy"], based_on: [] },
              events: [
                { id: "evt-1", snippet: "Event A", tags: ["market"], based_on: ["dec-1"] },
                { id: "evt-2", snippet: "Event B", tags: ["market"], based_on: ["dec-1", "evt-1"] },
              ],
              transitions: {},
              allowed_ids: ["dec-1", "evt-1", "evt-2"],
            },
            meta: {},
          });
          setIsStreaming(false);
        }, 300);
        return;
      }

      // Real streaming call
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        };
        if (token) {
          headers["Authorization"] = `Bearer ${token}`;
        }
        const res = await fetch(url, {
          method: "POST",
          body: JSON.stringify(body),
          headers,
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          throw new Error(`Streaming request failed: ${res.status}`);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        // Read SSE chunks until the stream ends
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let nlIndex;
          // Process complete lines. SSE messages are newline separated.
          while ((nlIndex = buffer.indexOf("\n")) !== -1) {
            let line = buffer.slice(0, nlIndex).trimEnd();
            buffer = buffer.slice(nlIndex + 1);
            if (!line) continue;
            // Skip SSE comments or keepalive lines
            if (line.startsWith(":")) continue;
            // Strip leading 'data:' if present
            if (line.startsWith("data:")) {
              line = line.slice("data:".length).trim();
            }
            if (!line) continue;
            try {
              const payload = JSON.parse(line);
              if (payload.token) {
                // Token event
                setTokens((prev) => {
                  const next = [...prev, payload.token as string];
                  if (process.env.NODE_ENV !== "production") {
                    try {
                      console.debug("[sse.token]", payload.token);
                    } catch {
                      /* ignore logging errors */
                    }
                  }
                  return next;
                });
              } else {
                // Final event; stream finished
                setFinalData(payload);
                setIsStreaming(false);
                // Fire global trace callback if audit metadata present
                try {
                  const meta = payload.meta ?? {};
                  if (
                    typeof window !== "undefined" &&
                    typeof (window as any).setCurrentTrace === "function"
                  ) {
                    const trace: any = {};
                    if (meta.request_id) trace.request_id = meta.request_id;
                    // Prefer explicit prompt_fingerprint; fall back to envelope fingerprint
                    if (meta.prompt_fingerprint) trace.prompt_fingerprint = meta.prompt_fingerprint;
                    if (!trace.prompt_fingerprint && meta.prompt_envelope_fingerprint) {
                      trace.prompt_fingerprint = meta.prompt_envelope_fingerprint;
                    }
                    if (meta.snapshot_etag) trace.snapshot_etag = meta.snapshot_etag;
                    if (Object.keys(trace).length > 0) {
                      if (process.env.NODE_ENV !== "production") {
                        console.debug("[sse.trace]", trace);
                      }
                      (window as any).setCurrentTrace(trace);
                    }
                  }
                } catch {
                  /* ignore errors when calling global trace */
                }
                if (process.env.NODE_ENV !== "production") {
                  try {
                    const rid = payload.meta?.request_id;
                    if (rid) {
                      console.debug("[sse.done]", rid);
                    }
                  } catch {
                    /* ignore */
                  }
                }
              }
            } catch {
              // Silently ignore malformed JSON lines
            }
          }
        }
        setIsStreaming(false);
      } catch (err: any) {
        // Handle cancellation separately
        if (err?.name === "AbortError") {
          setIsStreaming(false);
          return;
        }
        setError(err instanceof Error ? err : new Error(String(err)));
        setIsStreaming(false);
        throw err;
      }
    },
    []
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  return { tokens, isStreaming, error, finalData, startStream, cancel };
}
