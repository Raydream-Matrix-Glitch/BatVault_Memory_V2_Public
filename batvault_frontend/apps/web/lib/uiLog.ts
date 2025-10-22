export type UILogPayload = Record<string, unknown>;

export function uiLog(event: string, payload: UILogPayload = {}) {
  try {
    // If a structured logger is present (e.g., wired to OTEL), use it.
    // Otherwise fall back to console to keep things observable in dev.
    // @ts-ignore
    if (typeof window !== "undefined" && window.__bv_log) {
      // @ts-ignore
      const base = { ts: new Date().toISOString(), level: "INFO", service: "frontend", event, ...payload };
      window.__bv_log(base);
    } else {
      // eslint-disable-next-line no-console
      const base = { ts: new Date().toISOString(), level: "INFO", service: "frontend", event, ...payload };
      console.info("[ui]", base);
    }
  } catch {
    // noop — never throw from logging
  }
}