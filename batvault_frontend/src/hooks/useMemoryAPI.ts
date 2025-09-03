import { useCallback, useMemo } from "react";
import { useSSE } from "./useSSE";
import { logEvent } from "../utils/logger";

type AskOptions = Record<string, unknown> | undefined;

/**
 * Resolve the gateway base URL. Falls back to window.origin when env
 * var is not provided. Trailing slashes are trimmed.
 */
function resolveBase(): string {
  // Vite-style import.meta.env; tolerate undefined in tests.
  const envBase =
    (typeof import.meta !== "undefined" &&
      (import.meta as any)?.env?.VITE_GATEWAY_BASE) ||
    undefined;
  const base = envBase || (typeof window !== "undefined" ? window.location.origin : "");
  return String(base).replace(/\/+$/, "");
}

/**
 * Retrieve the bearer token. We support two keys to remain compatible
 * with older local setups.
 */
function getToken(): string | undefined {
  try {
    return (
      localStorage.getItem("batvault_token") ||
      localStorage.getItem("token") ||
      undefined
    ) as string | undefined;
  } catch {
    return undefined;
  }
}

/**
 * Wrapper around the streaming hook to build Memory API requests. It
 * encapsulates base URL resolution, bearer token retrieval and
 * endpoint selection for /v2/ask and /v2/query.
 */
export function useMemoryAPI() {
  const base = useMemo(resolveBase, []);

  const {
    tokens,
    isStreaming,
    error,
    finalData,
    startStream,
    cancel,
  } = useSSE();

  /**
   * Ask "why_decision" (or another intent) about a concrete decision.
   * The gateway requires either `anchor_id` or an `evidence` bundle.
   * We standardize on `anchor_id` for FE → BE calls.
   */
  const ask = useCallback(
    async (intent: string, decisionRef: string, options?: AskOptions) => {
      // NOTE: This is the line you called out. It is *not* truncated —
      // this is the full, correct statement.
      const payload = { intent, anchor_id: decisionRef, ...(options || {}) };

      // Structured UI log — safe, no PII.
      try {
        logEvent("ui.memory.ask.request", {
          intent,
          anchor_id_present: Boolean(decisionRef),
          has_options: Boolean(options && Object.keys(options).length),
        });
      } catch {
        /* noop */
      }

      const endpoint = `${base}/v2/ask?stream=true`;
      return startStream(endpoint, payload, getToken());
    },
    [base, startStream]
  );

  /**
   * Natural-language discovery endpoint; streams a semantic search / NL answer.
   */
  const query = useCallback(
    async (text: string) => {
      const payload = { text };
      try {
        logEvent("ui.memory.query.request", { text_len: (text || "").length });
      } catch {
        /* noop */
      }
      const endpoint = `${base}/v2/query?stream=true`;
      return startStream(endpoint, payload, getToken());
    },
    [base, startStream]
  );

  // Extract the request identifier from the final response metadata when available.
  // Some backends don’t include request_id in meta. Fall back to parsing it
  // from the bundle_url (/v2/bundles/{request_id}) if present.
  let requestId: string | undefined = (finalData as any)?.meta?.request_id;
  if (!requestId) {
    const bu = (finalData as any)?.bundle_url as string | undefined;
    if (bu && typeof bu === "string") {
      const m = bu.match(/\/v2\/bundles\/([^\/\s]+)$/i);
      if (m && m[1]) {
        requestId = m[1];
      }
    }
  }

  return {
    tokens,
    isStreaming,
    error,
    finalData,
    ask,
    query,
    cancel,
    requestId,
  };
}