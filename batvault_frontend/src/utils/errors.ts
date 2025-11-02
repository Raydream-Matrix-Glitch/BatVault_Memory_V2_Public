export function normalizeErrorMessage(raw: unknown): string {
  // Prefer structured Error-like objects first
  if (raw && typeof raw === "object") {
    const r = raw as any;
    const name = typeof r.name === "string" ? r.name : "Error";
    const statusPart = typeof r.status === "number" ? ` ${r.status}` : "";
    const msgPart = typeof r.message === "string" ? r.message : "";
    const detailPart =
      r?.detail != null
        ? (typeof r.detail === "string" ? r.detail : safeJson(r.detail))
        : "";
    const composed = [msgPart, detailPart].filter(Boolean).join(" — ");
    if (name !== "Error" || statusPart || composed) {
      return `${name}${statusPart}${composed ? ": " + composed : ""}`;
    }
  }

  // Stringify anything else
  const s = String(raw ?? "");

  // If the string contains a JSON object, try to parse it
  const braceIdx = s.indexOf("{");
  if (braceIdx >= 0) {
    try {
      const obj = JSON.parse(s.slice(braceIdx));
      // Common server payload shapes: { detail }, { error }, { message }
      const primary =
        pickFirstString(obj, ["detail", "error", "message"]) ??
        // Sometimes nested e.g. { detail: { detail: "..." } }
        pickNestedString(obj, ["detail", "error"], "detail");

      if (primary) return primary;

      // Map some common error codes/messages to readable text
      if (obj?.error === "bundle_not_found" || obj?.code === "NoSuchKey") {
        return "Bundle object not found in storage (NoSuchKey)";
      }
      if (obj?.error === "missing_headers" || /missing[_\s-]?headers/i.test(s)) {
        if (/policy[_\s-]?key/i.test(s) || /x-?policy-?key/i.test(JSON.stringify(obj))) {
          return "Missing X-Policy-Key";
        }
        return "Missing required headers";
      }
      if (/policy[_\s-]?key[_\s-]?mismatch/i.test(s)) {
        return "Policy key mismatch";
      }
    } catch {
      // ignore JSON parse errors gracefully
    }
  }

  // String pattern fallbacks
  if (/^\s*Not\s*Found\s*$/i.test(s)) return "Not found (bundle or route)";
  if (/NoSuchKey/i.test(s)) return "Bundle object not found in storage (NoSuchKey)";
  if (/missing[_\s-]?headers/i.test(s)) {
    if (/policy[_\s-]?key/i.test(s)) return "Missing X-Policy-Key";
    return "Missing required headers";
  }
  if (/policy[_\s-]?key[_\s-]?mismatch/i.test(s)) return "Policy key mismatch";
  if (/acl:domain_missing/i.test(s)) return "Anchor has no domain.";
  if (/acl:domain_mismatch/i.test(s)) return "Anchor domain mismatch.";
  if (/no anchor found/i.test(s)) return "No matching anchor found";

  return s;
}

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

function safeJson(v: unknown): string {
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function pickFirstString(obj: any, keys: string[]): string | undefined {
  for (const k of keys) {
    const v = obj?.[k];
    if (typeof v === "string" && v) return v;
  }
  return undefined;
}

function pickNestedString(obj: any, parents: string[], child: string): string | undefined {
  for (const p of parents) {
    const v = obj?.[p];
    if (v && typeof v === "object" && typeof v[child] === "string" && v[child]) {
      return v[child];
    }
  }
  return undefined;
}
