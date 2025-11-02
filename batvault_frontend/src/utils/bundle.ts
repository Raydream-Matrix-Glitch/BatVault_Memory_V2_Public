// V3 bundle helpers. Endpoints live under /v3/bundles; Gateway is SoT.
import { getRuntimeConfig } from "../config/runtime";
import { logEvent } from "./logger";

// Hard fallback – used only when /config doesn’t tell us what to do.
const FALLBACK_BUNDLE_NAMES = ["bundle_view", "bundle_full"] as const;

/**
 * Read archive names from the runtime /config the Gateway serves.
 * If the backend hasn't been upgraded yet, fall back to the local list.
 */
function readBundleNamesFromConfig(): string[] {
  try {
    const cfg: any = getRuntimeConfig();
    if (Array.isArray(cfg?.bundle_archives) && cfg.bundle_archives.length > 0) {
      return cfg.bundle_archives.map((x: unknown) => String(x));
    }
  } catch {
    // runtime config not ready yet – fall back
  }
  return [...FALLBACK_BUNDLE_NAMES];
}

// The only valid archive names produced by the uploader and accepted by the Gateway.
// Primary source of truth is now the Gateway /config; we keep the local list as a deterministic fallback.
export const ALLOWED_BUNDLE_NAMES = readBundleNamesFromConfig() as readonly string[];
export type BundleName = (typeof ALLOWED_BUNDLE_NAMES)[number];

/**
 * Some callers still hand us 32-hex transport/trace ids.
 * MinIO (and the screenshot) clearly shows objects under 16-hex prefixes.
 * We therefore centralise the logic here – this file is authoritative.
 */
export function normalizeRid(rid: string | null | undefined): string {
  const v = (rid || "").trim();
  if (!v) return "";
  if (v.length === 16) return v;
  if (v.length === 32) {
    const shortened = v.slice(-16);
    logEvent("ui.bundle.rid_normalized", { from: v, to: shortened });
    return shortened;
  }
  // weird length – keep as-is but log once
  logEvent("ui.bundle.rid_weird_len", { rid: v, len: v.length });
  return v;
}

/** Telemetry-only guard for odd RIDs (non 16/32) */
function logSuspiciousRid(rid: string | undefined | null) {
  if (!rid) return;
  if (rid.length !== 16 && rid.length !== 32) {
    // deterministic, no swallow
    logEvent("ui.bundle.suspicious_rid", { rid, len: rid.length });
  }
}

/** Small jittered sleep to avoid thundering herds on first click. */
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const delayMs = (attempt: number) => {
  const base = Math.min(1200, 100 * Math.pow(2, attempt - 1));
  return Math.floor(base / 2 + Math.random() * base / 2);
};

/** For full-page navigations (window.open):
 * Always prefer an ABSOLUTE base advertised by runtime /config.
 * In dev (:5173), same-origin paths hit the SPA and show "Page not found".
 */
function navBaseForNewTab(cfg: any): string {
  const adv = (cfg && cfg.gateway_base) ? String(cfg.gateway_base) : "";
  if (!adv) return "";
  return adv.replace(/\/$/, "");
}

/** Helper to get both gateway_base and the /v3/bundles path. */
function bundlesBaseFromConfig(): { base: string; path: string } {
  const cfg = getRuntimeConfig();
  const base = navBaseForNewTab(cfg);
  const path = (cfg.endpoints?.bundles || "/v3/bundles").replace(/^\/?/, "/");
  return { base, path };
}

export function isBundleName(x: string): x is BundleName {
  return (ALLOWED_BUNDLE_NAMES as readonly string[]).includes(x);
}

export function assertBundleName(x: string): asserts x is BundleName {
  if (!isBundleName(x)) {
    // Fail fast on the client instead of leaking traffic to a 422 path.
    throw new Error(`invalid_bundle_name:${x}`);
  }
}

/** Build the canonical download URL. Keep this as the only place that formats /download. */
export function buildDownloadUrl(rid: string, name: BundleName): string {
  const nrid = normalizeRid(rid);
  logSuspiciousRid(nrid);
  const { base, path } = bundlesBaseFromConfig();
  return `${base}${path}/${encodeURIComponent(nrid)}/download?name=${name}`;
}

/** Build the base URL for raw artifacts (e.g. /receipt.json). */
export function buildBundleBaseUrl(rid: string): string {
  const nrid = normalizeRid(rid);
  logSuspiciousRid(nrid);
  const { base, path } = bundlesBaseFromConfig();
  return `${base}${path}/${encodeURIComponent(nrid)}`;
}

/** One-shot presign call. Exported, so all callers go through here. */
export async function presignOnce(
  rid: string,
  name: BundleName
): Promise<{ ok: boolean; url?: string; status?: number }> {
  assertBundleName(name);
  const nrid = normalizeRid(rid);
  logSuspiciousRid(nrid);
  const { base, path } = bundlesBaseFromConfig();
  const u = `${base}${path}/${encodeURIComponent(nrid)}/download?name=${name}`;
  const res = await fetch(u, { method: "POST", credentials: "include" });
  if (!res.ok) return { ok: false, status: res.status };
  const data = (await res.json()) as { url?: string; expires_in?: number };
  return { ok: true, url: data?.url };
}

/**
 * Diagnostic head check – used by the old drawer flow.
 * We keep it for compatibility, but new code should prefer direct GET of the artifact.
 */
export async function headExists(rid: string, name: string): Promise<boolean> {
  const nrid = normalizeRid(rid);
  const cfg = getRuntimeConfig();
  const bbase = navBaseForNewTab(cfg);
  const u = `${bbase}/v3/ops/minio/head/${encodeURIComponent(nrid)}?name=${encodeURIComponent(name)}`;
  const res = await fetch(u, { credentials: "include" });
  if (!res.ok) return false;
  try {
    const j = await res.json();
    return !!j?.exists;
  } catch {
    return false;
  }
}

/**
 * Fetch the bundle JSON map directly from the gateway.
 * This is what the audit drawer wants – centralised here so it also benefits from RID normalisation.
 */
export async function fetchBundleMap(rid: string): Promise<any | null> {
  const base = buildBundleBaseUrl(rid);
  const res = await fetch(base, { credentials: "include" });
  if (!res.ok) {
    logEvent("ui.bundle.fetch_failed", { rid: normalizeRid(rid), status: res.status });
    return null;
  }
  try {
    return await res.json();
  } catch {
    return null;
  }
}

/**
 * Given the JSON map returned by /v3/bundles/{rid} (i.e. what the audit drawer fetches),
 * extract the *bundle-authored* meta. This is the source of truth.
 *
 * - prefers bundle.response.meta
 * - falls back to the provided meta (usually UI-level meta)
 * - normalises flat vs fingerprints.* layouts
 */
export function extractEffectiveMetaFromBundle(
  bundleOrMap: any,
  fallback?: any
): any {
  // drawer passes the parsed response.json to verifyAll, so we mostly see this shape:
  const respMeta =
    bundleOrMap &&
    typeof bundleOrMap === "object" &&
    (bundleOrMap as any).response &&
    (bundleOrMap as any).response.meta
      ? (bundleOrMap as any).response.meta
      : null;
  const m = respMeta && typeof respMeta === "object" ? respMeta : fallback || null;
  if (!m) return null;
  return {
    ...m,
    fingerprints: {
      ...(m.fingerprints ?? {}),
      graph_fp: m.graph_fp ?? m.fingerprints?.graph_fp,
      bundle_fp: m.bundle_fp ?? m.fingerprints?.bundle_fp,
      allowed_ids_fp: m.allowed_ids_fp ?? m.fingerprints?.allowed_ids_fp,
    },
  };
}

/**
 * Open the presigned bundle in a new tab. Used by the audit drawer actions.
 */
export async function openPresignedBundle(
  requestId: string,
  name: BundleName,
  opts?: { newTab?: boolean }
): Promise<void> {
  assertBundleName(name);
  const nrid = normalizeRid(requestId);
  logEvent("ui.bundle.download.click", { raw_rid: requestId, rid: nrid, name });
  logSuspiciousRid(nrid);
  const ps = await presignOnce(nrid, name);
  if (!ps.ok || !ps.url) {
    logEvent("ui.bundle.presign_failed", { rid: nrid, name, status: ps.status ?? null });
    return;
  }
  const w = window.open(ps.url, opts?.newTab ? "_blank" : "_self", "noopener,noreferrer");
  logEvent("ui.bundle.open_window", { rid: nrid, name, opened: !!w, via: "presign" });
}

function logOpenedReceipt(rid: string, via: string, name: string = "receipt.json", opened: boolean) {
  logEvent("ui.bundle.open_window", { rid, name, opened, via });
}
/**
 * Open receipt.json, falling back to presign if the direct proxy path fails.
 * This is the flow used by the audit drawer – do NOT reimplement it there.
 */
export async function openReceipt(requestId: string): Promise<void> {
  const nrid = normalizeRid(requestId);
  const { base, path } = bundlesBaseFromConfig();
  const receiptName = "receipt.json";
  const presignUrl = `${base}${path}/${encodeURIComponent(nrid)}/download?name=${encodeURIComponent(receiptName)}`;

  // 1) ask gateway to presign the actual artifact
  const first = await fetch(presignUrl, { method: "POST", credentials: "include" });
  if (first.ok) {
    const body = await first.json();
    if (body?.url) {
      const w = window.open(body.url, "_blank", "noopener,noreferrer");
      logOpenedReceipt(nrid, "presign_artifact", receiptName, !!w);
      return;
    }
  }

  // 2) gateway said “pending” – poll ops/head a couple of times, then retry presign
  for (let attempt = 1; attempt <= 4; attempt++) {
    const existsArtifact = await headExists(nrid, receiptName);
    const existsBundle = await headExists(nrid, "bundle_view");
    if (existsArtifact || existsBundle) {
      const retry = await fetch(presignUrl, { method: "POST", credentials: "include" });
      if (retry.ok) {
        const body = await retry.json();
        if (body?.url) {
          const w = window.open(body.url, "_blank", "noopener,noreferrer");
          logOpenedReceipt(nrid, "presign_artifact_after_head", receiptName, !!w);
          return;
        }
      }
    }
    await sleep(delayMs(attempt));
  }

  // 3) final fallback – open the view bundle
  const ps = await presignOnce(nrid, "bundle_view");
  if (ps.ok && ps.url) {
    const w = window.open(ps.url, "_blank", "noopener,noreferrer");
    logOpenedReceipt(nrid, "presign_last_resort", "bundle_view", !!w);
 }
}
