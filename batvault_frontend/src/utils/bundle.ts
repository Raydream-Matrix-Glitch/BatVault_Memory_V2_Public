// V3 bundle helpers. No orphan logic; endpoints under /v3/bundles

export async function openEvidenceBundle(requestId?: string, bundleUrl?: string): Promise<void> {
  const base = import.meta.env.VITE_GATEWAY_BASE || '';
  const mkAbs = (p: string) => (p.startsWith('http') ? p : `${base}${p}`);
  let url = bundleUrl;
  if (!url && requestId) {
    url = mkAbs(`/v3/bundles/${encodeURIComponent(requestId)}`);
  }
  if (!url) throw new Error("No bundle URL or request_id provided");
  window.open(url, "_blank", "noopener,noreferrer");
}