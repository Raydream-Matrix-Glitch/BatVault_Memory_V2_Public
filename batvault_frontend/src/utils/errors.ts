export function normalizeErrorMessage(raw: unknown): string {
  const s = String(raw || "");
  const idx = s.indexOf("{");
  if (idx >= 0) {
    try {
      const obj = JSON.parse(s.slice(idx));
      const detail = (obj && (obj.detail || obj.error || obj.message)) || null;
      if (typeof detail === "string" && detail) return detail;
      if (detail && typeof detail === "object" && typeof detail.detail === "string") {
        const d = detail.detail as string;
        const cands = Array.isArray((detail as any).candidates) ? (detail as any).candidates : [];
        return cands.length ? `${d}: ${cands.join(", ")}` : d;
      }
    } catch { /* ignore JSON parse */ }
  }
  if (/missing[_\s-]?headers/i.test(s)) {
    if (/policy[_\s-]?key/i.test(s)) return "Missing X-Policy-Key";
    return "Missing required headers";
  }
  if (/policy[_\s-]?key[_\s-]?mismatch/i.test(s)) return "Policy key mismatch";
  if (/no anchor found/i.test(s)) return "No matching anchor found";
  return s;
}