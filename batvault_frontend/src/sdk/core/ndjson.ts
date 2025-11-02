/**
 * sdk/core/ndjson.ts
 * Generic ND-JSON streaming POST helper with abort, simple back-pressure,
 * and structured header propagation. No broad try/catch; errors are typed.
 */
import { queryEndpoint } from "../../traceGlobals";
import { buildPolicyHeaders } from "../../utils/policy";

export type NDJSONToken = Record<string, unknown> & { evt?: string; token?: string };

export interface StreamCallbacks<TFinal=unknown> {
  onToken?: (tok: NDJSONToken) => void;
  onFinal?: (final: TFinal) => void;
  onError?: (err: StreamError) => void;
  onHeaders?: (h: Headers) => void;
  onProgress?: (info: { bytes: number; lines: number }) => void;
}

export interface StreamError {
  name: "PreconditionFailed" | "PolicyMismatch" | "HttpError" | "NetworkError" | "ParseError";
  status?: number;
  message: string;
  detail?: unknown;
  headers?: Headers;
}

export interface StreamOptions {
  endpoint?: string;
  snapshotEtag?: string; // for X-Snapshot-ETag precondition
  requestId?: string;
  extraHeaders?: Record<string,string>;
  reconnectOnPolicyMismatch?: boolean; // single automatic retry
  highWaterMark?: number; // lines per flush to caller
  signal?: AbortSignal;
}

/**
 * Posts a JSON body and consumes an ND-JSON response.
 * Returns an AbortController for the active request and a promise that resolves when stream closes.
 */
export function postNDJSON<TFinal=unknown>(
  body: unknown,
  callbacks: StreamCallbacks<TFinal>,
  opts: StreamOptions = {}
): { controller: AbortController; done: Promise<void> } {
  const endpoint = opts.endpoint || queryEndpoint();
  const ctrl = new AbortController();
  const signal = opts.signal ?? ctrl.signal;
  const headers = {
    "Content-Type": "application/json",
    "Accept": "application/x-ndjson", // ensure server returns ND-JSON stream
    ...buildPolicyHeaders(opts.requestId),
    ...(opts.extraHeaders || {}),
  } as Record<string,string>;
  if (opts.snapshotEtag) headers["X-Snapshot-ETag"] = opts.snapshotEtag;

  async function run(allowRetry: boolean): Promise<void> {
    let res: Response;
    try {
      res = await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal
      });
    } catch (err) {
      const msg = (err && (err as any).message) ? (err as any).message : String(err);
      callbacks.onError?.({ name: "NetworkError", message: `Failed to fetch ${endpoint}: ${msg}` });
      return;
    }

    callbacks.onHeaders?.(res.headers);

    // Precondition failed (snapshot drift) should be surfaced; SDK doesn't auto-refresh ETag here.
    if (res.status === 412) {
      const err: StreamError = { name: "PreconditionFailed", status: 412, message: "Snapshot precondition failed", headers: res.headers };
      callbacks.onError?.(err);
      return;
    }

    if (res.status === 409) {
      let code = "", detailTxt = "";
      try {
        const ctype = (res.headers.get("content-type") || "").toLowerCase();
        if (ctype.includes("application/json")) {
          const j = await res.json();
          code = (j?.error?.code || j?.error || j?.detail || "").toString();
          detailTxt = JSON.stringify(j);
        } else {
          detailTxt = await res.text().catch(() => "");
        }
      } catch { /* ignore parse errors */ }
      const err: StreamError = {
        name: "PolicyMismatch",
        status: 409,
        message: "Gateway rejected mixed-policy composition",
        detail: { code, body: detailTxt },
        headers: res.headers
      };
      callbacks.onError?.(err);
      return;
    }

    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      const err: StreamError = { name: "HttpError", status: res.status, message: `HTTP ${res.status} at ${endpoint}`, detail: txt, headers: res.headers };
      callbacks.onError?.(err);
      return;
    }

    // Adopt server policy for subsequent calls (even if we don't reconnect).
    const observedPolicy = res.headers.get("X-BV-Policy-Fingerprint") || undefined;
    if (observedPolicy && (window as any)?.BV_POLICY_KEY !== observedPolicy) {
      try { (window as any).BV_POLICY_KEY = observedPolicy; } catch {}
    }
    // Also adopt allowed-ids fingerprint for cache keys
    const observedAllowed = res.headers.get("X-BV-Allowed-Ids-FP") || undefined;
    if (observedAllowed && (window as any)?.BV_ALLOWED_IDS_FP !== observedAllowed) {
      try { (window as any).BV_ALLOWED_IDS_FP = observedAllowed; } catch {}
    }
    // Policy mismatch hint via header (server still processed). Optional single reconnect.
    const providedPolicyKey = headers["X-Policy-Key"] || "";
    const policyMismatch = Boolean(observedPolicy && providedPolicyKey === "probe");
    if (opts.reconnectOnPolicyMismatch && policyMismatch && allowRetry) {
      const err: StreamError = { name: "PolicyMismatch", message: "Server computed a policy_fp; retrying once.", headers: res.headers };
      callbacks.onError?.(err);
      ctrl.abort();
      return run(false);
    }

    const reader = res.body?.getReader();
    if (!reader) {
      callbacks.onError?.({ name: "NetworkError", message: "ReadableStream not available" });
      return;
    }

    const decoder = new TextDecoder();
    let buf = "";
    let lines = 0;
    let bytes = 0;
    const HWM = Math.max(1, opts.highWaterMark || 50);

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (value) {
        bytes += value.byteLength;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split(/\r?\n/);
        buf = parts.pop() || "";
        for (const line of parts) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const tok = JSON.parse(trimmed) as NDJSONToken;
            callbacks.onToken?.(tok);
          } catch (e) {
            callbacks.onError?.({ name: "ParseError", message: "Failed to parse ND-JSON line", detail: { line: trimmed } });
          }
          lines++;
          if (lines % HWM === 0) callbacks.onProgress?.({ bytes, lines });
        }
      }
    }
    if (buf.trim().length > 0) {
      try {
        const final = JSON.parse(buf) as TFinal;
        callbacks.onFinal?.(final);
      } catch {
        callbacks.onError?.({ name: "ParseError", message: "Failed to parse final JSON chunk", detail: { tail: buf.slice(0, 200) } });
      }
    }
    callbacks.onProgress?.({ bytes, lines });
  }

  const done = run(true);
  return { controller: ctrl, done };
}