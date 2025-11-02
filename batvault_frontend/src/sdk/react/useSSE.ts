import { useCallback, useRef, useState } from "react";
import { postNDJSON, NDJSONToken, StreamError } from "../core/ndjson";

export interface UseSSEState<TFinal = unknown> {
  isStreaming: boolean;
  tokens: NDJSONToken[];
  final: TFinal | null;
  error: StreamError | null;
  progress: { bytes: number; lines: number };
  headers?: Headers;
}

export interface UseSSEOptions {
  endpoint?: string;
}

/** Lightweight UI breadcrumb sink to prove hydration runs via Gateway. */
function uiLog(event: string, payload?: Record<string, unknown>) {
  try {
    const body = JSON.stringify({ event, ...payload });
    // Prefer non-blocking beacon when available
    // @ts-ignore - sendBeacon isn't in TS DOM lib for all targets
    if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
      // @ts-ignore
      navigator.sendBeacon("/v2/ui/logs", body);
    } else {
      fetch("/v2/ui/logs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
        keepalive: true,
      }).catch(() => {});
    }
  } catch {
    /* non-fatal */
  }
}

export function useSSE<TFinal = unknown>(opts: UseSSEOptions = {}) {
  const [state, setState] = useState<UseSSEState<TFinal>>({
    isStreaming: false,
    tokens: [],
    final: null,
    error: null,
    progress: { bytes: 0, lines: 0 },
    headers: undefined,
  });

  // Keep an AbortController per active stream
  const ctrlRef = useRef<AbortController | null>(null);

  /** Start a new NDJSON stream. */
  const start = useCallback((body: unknown) => {
    // Abort any prior stream (idempotent)
    if (ctrlRef.current) {
      try {
        ctrlRef.current.abort();
      } catch {
        /* ignore */
      }
      ctrlRef.current = null;
    }

    setState({
      isStreaming: true,
      tokens: [],
      final: null,
      error: null,
      progress: { bytes: 0, lines: 0 },
      headers: undefined,
    });

    uiLog("fe.query.stream.start", { endpoint: opts.endpoint || "/v3/query" });

    // Kick off the stream. postNDJSON provides controller + completion promise.
    const { controller, done } = postNDJSON<TFinal>(body, {
      onToken: (tok) =>
        setState((s) => ({ ...s, tokens: [...s.tokens, tok] })),
      onFinal: (final) => {
        setState((s) => ({ ...s, final, isStreaming: false }));
        uiLog("fe.query.stream.final");
      },
      onProgress: (p) => setState((s) => ({ ...s, progress: p })),
      onError: (err) => {
        setState((s) => ({ ...s, error: err, isStreaming: false }));
        // Keep the log small and structured
        uiLog("fe.query.stream.error", {
          name: err?.name,
          message: err?.message,
          code: (err as any)?.code,
        });
      },
      onHeaders: (h) => {
        setState((s) => ({ ...s, headers: h }));
        // Breadcrumb with helpful correlation headers, if present
        try {
          uiLog("fe.query.stream.headers", {
            snapshot_etag: h.get("x-snapshot-etag") || undefined,
            policy_fp: h.get("x-bv-policy-fingerprint") || undefined,
          });
        } catch {
          /* ignore */
        }
      },
      // If your postNDJSON supports endpoint override, pass it through:
      // endpoint: opts.endpoint,
    });

    ctrlRef.current = controller;

    // Surface completion to callers if they want to await it
    return { controller, done };
  }, [opts.endpoint]);

  /** Abort the active stream, if any, and reset flags. */
  const abort = useCallback(() => {
    if (ctrlRef.current) {
      try {
        ctrlRef.current.abort();
      } catch {
        /* ignore */
      }
      ctrlRef.current = null;
    }
    setState((s) => ({
      ...s,
      isStreaming: false,
    }));
    uiLog("fe.query.stream.abort");
  }, []);

  /**
   * Hydrate the hook from a persisted report (e.g., after navigation).
   * Useful for re-displaying prior results without re-streaming.
   */
  const fromReport = useCallback((report: { tokens?: NDJSONToken[]; final?: TFinal }) => {
    const { tokens, final } = report || {};
    if (ctrlRef.current) {
      try {
        ctrlRef.current.abort();
      } catch {
        /* ignore */
      }
      ctrlRef.current = null;
    }
    setState({
      isStreaming: false,
      tokens: tokens || [],
      final: final ?? null,
      error: null,
      progress: { bytes: 0, lines: tokens?.length || 0 },
      headers: undefined,
    });
  }, []);

  return { start, abort, fromReport, state };
}
