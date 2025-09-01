/**
 * Structured UI logging with deterministic IDs.
 * - JSON-first events (suitable for backends & local dev)
 * - Deterministic ui_event_id derived from stable hash of inputs
 * - Best-effort delivery via navigator.sendBeacon (falls back to fetch)
 * - Zero external deps
 */
type UIEventPayload = Record<string, unknown> | undefined;

/** Simple, deterministic FNV-1a hash to hex (stable across sessions). */
function fnv1aHex(str: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h += (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24);
  }
  // convert to unsigned and hex
  return (h >>> 0).toString(16).padStart(8, "0");
}

/** Build a deterministic id from event name + salient fields. */
function deterministicId(name: string, payload: UIEventPayload): string {
  try {
    const ctx = getCtx();
    const key = JSON.stringify({
      name,
      // common correlators (stable per response)
      request_id: ctx.request_id ?? null,
      prompt_fingerprint: ctx.prompt_fingerprint ?? null,
      bundle_fingerprint: ctx.bundle_fingerprint ?? null,
      snapshot_etag: ctx.snapshot_etag ?? null,
      // salient payload bits (id, tag, action)
      id: (payload as any)?.id ?? null,
      tag: (payload as any)?.tag ?? null,
      action: (payload as any)?.action ?? null,
    });
    return `${fnv1aHex(name)}_${fnv1aHex(key)}`;
  } catch {
    // never throw from logger
    return `${fnv1aHex(name)}_${fnv1aHex(String(Math.random()))}`;
  }
}

/** Pull meta context from the last finalized response exposed on window. */
function getCtx(): {
  request_id?: string;
  prompt_fingerprint?: string;
  bundle_fingerprint?: string;
  snapshot_etag?: string;
} {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const finalData: any = (globalThis as any).__BATVAULT_FINAL__ ?? undefined;
  const meta = finalData?.meta ?? {};
  return {
    request_id: meta?.request_id,
    prompt_fingerprint: meta?.prompt_fingerprint,
    bundle_fingerprint: meta?.bundle_fingerprint,
    snapshot_etag: meta?.snapshot_etag,
  };
}

interface LogOptions {
  /** When true, also POST the JSON to the backend (/v2/ui/logs) using sendBeacon/fetch. */
  sendToBackend?: boolean;
  /** Optional override path for backend logging. Default: /v2/ui/logs */
  path?: string;
}

/** Core logging function. */
export function logEvent(name: string, payload: UIEventPayload = {}, opts: LogOptions = {}): string {
  const ts = new Date().toISOString();
  const ctx = getCtx();
  const ui_event_id = deterministicId(name, payload);
  const event = {
    ui_event_id,
    ts,
    level: "INFO",
    service: "frontend",
    page: "memory",
    name,
    ctx,
    payload,
  };

  // Always log to console in dev; in prod only log compact line to keep noise down.
  try {
    if (process.env.NODE_ENV !== "production") {
      // eslint-disable-next-line no-console
      console.log("[ui.event]", event);
    } else {
      // eslint-disable-next-line no-console
      console.log("[ui.event]", { ui_event_id, name, ts, ...ctx, payload });
    }
  } catch {
    /* no-op */
  }

  // Optionally ship to backend
  if (opts.sendToBackend) {
    const path = opts.path ?? "/v2/ui/logs";
    try {
      const body = JSON.stringify(event);
      const ok = !!(navigator as any)?.sendBeacon?.(path, new Blob([body], { type: "application/json" }));
      if (!ok) {
        // Fallback to non-blocking fetch
        fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body }).catch(() => {});
      }
    } catch {
      /* ignore transport errors */
    }
  }

  return ui_event_id;
}

/** Back-compat shim for older calls. */
export const log = (event: string, payload?: any) => logEvent(event, payload);