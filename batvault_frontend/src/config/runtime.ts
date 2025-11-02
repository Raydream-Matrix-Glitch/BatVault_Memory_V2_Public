import { PublicConfig } from "./schema";

declare global {
  interface Window { __BV_CONFIG?: PublicConfig }
}

/** Load runtime config from the Gateway. Validates shape and caches on window.__BV_CONFIG. */
export async function loadRuntimeConfig(): Promise<PublicConfig> {
  if (window.__BV_CONFIG) return window.__BV_CONFIG!;
  // Prefer SAME-ORIGIN by default so requests flow through the API Edge.
  // Only use an absolute base if explicitly provided *and* not pointing at the internal "gateway" host.
  const envBaseRaw = String(import.meta.env.VITE_GATEWAY_BASE || import.meta.env.VITE_API_BASE || "").trim();
  // Treat internal/localhost bases as "internal": always go same-origin to preserve Edge passthrough & logs.
  const isInternalGateway =
    /^https?:\/\/(gateway(?:\.[\w.-]+)?)(?::\d+)?(\/|$)/i.test(envBaseRaw) ||
    /^https?:\/\/(localhost|127\.0\.0\.1)(?::\d+)?(\/|$)/i.test(envBaseRaw);
  const allowAbsolute = String(import.meta.env.VITE_ALLOW_ABSOLUTE_GATEWAY || "").trim() === "1";
  const base = (!envBaseRaw || isInternalGateway || !allowAbsolute) ? "" : envBaseRaw;
  const url = `${base.replace(/\/$/, "")}/config`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Config bootstrap failed: HTTP ${res.status} ${res.statusText} ${detail}`);
  }
  // Guard against HTML/other content types → avoids "Unexpected token '<'".
  const ctype = res.headers.get("content-type") || "";
  if (!ctype.includes("application/json")) {
    const body = await res.text().catch(() => "");
    const preview = body.slice(0, 400).replace(/\s+/g, " ");
    throw new Error(
      `Config bootstrap failed: expected application/json from /config, got ${ctype || "unknown"}. First bytes: ${preview}`
    );
  }
  const raw = await res.json();
  // inline validation; if shape changes we fail early and loud
  const { PublicConfig: Schema } = await import("./schema");
  const parsed = Schema.safeParse(raw);
  if (!parsed.success) {
    throw new Error("Config bootstrap failed: invalid payload");
  }
  window.__BV_CONFIG = parsed.data;
  return parsed.data;
}

/** Read the current runtime config after loadRuntimeConfig() has been called. */
export function getRuntimeConfig(): PublicConfig {
  const cfg = window.__BV_CONFIG;
  if (!cfg) throw new Error("Runtime config not initialized – call loadRuntimeConfig() in main.tsx first.");
  return cfg;
}