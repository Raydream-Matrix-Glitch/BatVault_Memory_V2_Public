export type UILogPayload = Record<string, unknown>;

export function uiLog(event: string, payload: UILogPayload = {}) {
  try {
    // If a structured logger is present (e.g., wired to OTEL), use it.
    // Otherwise fall back to console to keep things observable in dev.
    // @ts-ignore
    if (typeof window !== "undefined" && window.__bv_log) {
      // @ts-ignore
      window.__bv_log({ level: "INFO", event, ...payload });
    } else {
      // eslint-disable-next-line no-console
      console.info("[ui]", event, payload);
    }
  } catch {
    // noop — never throw from logging
  }
}