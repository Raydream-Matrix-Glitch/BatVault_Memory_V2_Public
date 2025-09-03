import { logEvent } from "./logger";

/**
 * Force a browser download for the given URL. Respects Content-Disposition.
 */
async function forceDownload(url: string, fallbackName: string) {
  const resp = await fetch(url, { method: "GET" });
  if (!resp.ok) throw new Error(`Failed to download bundle: ${resp.status}`);
  const blob = await resp.blob();

  let filename = fallbackName;
  const disp = resp.headers.get("Content-Disposition") || resp.headers.get("content-disposition");
  if (disp) {
    const m = /filename\*=UTF-8''([^;]+)|filename="([^"]+)"|filename=([^;]+)/i.exec(disp);
    if (m) filename = decodeURIComponent((m[1] || m[2] || m[3] || "").trim()).replace(/\s+/g, "_");
  }

  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
}

/**
 * Open/download the evidence bundle associated with a request.
 * Strategy:
 *  1) If we have a requestId → POST /v2/bundles/{rid}/download?format=tar → {url} → open.
 *  2) Else, if providedUrl exists → open it (server may serve JSON or TAR).
 *  3) Exec-friendly fallback: GET /v2/bundles/{rid}.tar and force download.
 *  4) Last resort: GET /v2/bundles/{rid} (JSON) and force download.
 */
export async function openEvidenceBundle(requestId?: string, providedUrl?: string) {
  // Try to recover a requestId from the provided URL if missing.
  if (!requestId && providedUrl) {
    const m = /\/v2\/bundles\/([^\/\s]+)/i.exec(providedUrl);
    if (m && m[1]) requestId = m[1];
  }

  // (1) Presigned MinIO TAR (preferred)
  if (requestId) {
    try { logEvent("ui.memory.bundle_download.presign_request", { rid: requestId }); } catch {}
    try {
      const resp = await fetch(`/v2/bundles/${requestId}/download?format=tar`, { method: "POST" });
      if (resp.ok) {
        const data = await resp.json().catch(() => ({} as any));
        const url = (data as any)?.url as string | undefined;
        if (url) {
          try { logEvent("ui.memory.bundle_download.presign_ok", { rid: requestId }); } catch {}
          window.open(url, "_blank");
          return;
        }
      } else {
        try { logEvent("ui.memory.bundle_download.presign_http_error", { rid: requestId, status: resp.status }); } catch {}
      }
    } catch (e) {
      try { logEvent("ui.memory.bundle_download.presign_failed", { rid: requestId, error: String(e) }); } catch {}
      // fall through
    }
  }

  // (2) Provided URL (often JSON endpoint)
  if (providedUrl) {
    try {
      try { logEvent("ui.memory.bundle_download.open_provided_url", { url: providedUrl }); } catch {}
      window.open(providedUrl, "_blank");
      return;
    } catch {
      // continue to fallback
    }
  }

  // (3) Exec-friendly TAR fallback
  if (requestId) {
    try {
      try { logEvent("ui.memory.bundle_download.fallback_tar", { rid: requestId, path: `/v2/bundles/${requestId}.tar` }); } catch {}
      await forceDownload(`/v2/bundles/${requestId}.tar`, `evidence-${requestId}.tar`);
      return;
    } catch (e) {
      try { logEvent("ui.memory.bundle_download.fallback_tar_failed", { rid: requestId, error: String(e) }); } catch {}
      // continue to JSON fallback
    }
  }

  // (4) JSON fallback
  if (requestId) {
    try {
      try { logEvent("ui.memory.bundle_download.fallback_json", { rid: requestId, path: `/v2/bundles/${requestId}` }); } catch {}
      await forceDownload(`/v2/bundles/${requestId}`, `evidence-${requestId}.json`);
    } catch (e) {
      try { logEvent("ui.memory.bundle_download.fallback_json_failed", { rid: requestId, error: String(e) }); } catch {}
    }
  }
}