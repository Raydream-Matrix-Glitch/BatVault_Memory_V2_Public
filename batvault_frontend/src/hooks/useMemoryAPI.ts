import { useCallback } from "react";
import { useSSE } from "./useSSE";

/**
 * Wrapper around the streaming hook to build Memory API requests. It
 * encapsulates the base URL resolution, bearer token retrieval and
 * endpoint selection for /v2/ask and /v2/query.
 */
export function useMemoryAPI() {
  const {
    tokens,
    isStreaming,
    error,
    finalData,
    startStream,
    cancel,
  } = useSSE();

  // Determine the base API prefix. Prefer VITE_API_BASE from the Vite env;
  // if unset, fall back to the current window origin when running in a browser.
  // In test environments where window is undefined, default to an empty string
  // so that paths remain relative (e.g. "/v2/ask"). Trailing slash is trimmed.
  const base: string = (() => {
    const envBase = (import.meta as any).env?.VITE_API_BASE as string | undefined;
    if (envBase) return envBase.replace(/\/$/, "");
    if (typeof window !== "undefined" && window.location) {
      return window.location.origin.replace(/\/$/, "");
    }
    return "";
  })();


  // Acquire the bearer token from localStorage. Note: this may return null.
  const getToken = (): string | undefined => {
    try {
      return localStorage.getItem("access_token") || undefined;
    } catch {
      return undefined;
    }
  };

  const ask = useCallback(
    async (
      intent: string,
      decisionRef: string,
      options?: Record<string, unknown>
    ) => {
      const payload = { intent, decision_ref: decisionRef, ...(options || {}) };
      const endpoint = `${base}/v2/ask`;
      return startStream(endpoint, payload, getToken());
    },
    [base, startStream]
  );

  const query = useCallback(
    async (text: string) => {
      const payload = { text };
      const endpoint = `${base}/v2/query`;
      return startStream(endpoint, payload, getToken());
    },
    [base, startStream]
  );

  // Extract the request identifier from the final response metadata when available.
  const requestId: string | undefined = finalData?.meta?.request_id;

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