/**
 * sdk/utils/idempotency.ts
 * Stable JSON stringify + SHA-256 based idempotency keys.
 * Stores the last key in sessionStorage so refresh+resubmit reuses it.
 */
const IDEM_KEY_SS = "bv:last_idempotency_key";

export function stableStringify(x: unknown): string {
  if (x === null || typeof x !== "object") return JSON.stringify(x);
  if (Array.isArray(x)) return `[${x.map(stableStringify).join(",")}]`;
  const entries = Object.entries(x as Record<string, unknown>).sort(([a],[b]) => a.localeCompare(b));
  return `{${entries.map(([k,v]) => JSON.stringify(k)+":"+stableStringify(v)).join(",")}}`;
}

async function sha256Hex(s: string): Promise<string> {
  const buf = new TextEncoder().encode(s);
  const hash = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(hash)).map(b=>b.toString(16).padStart(2,"0")).join("");
}

export async function idempotencyKey(body: unknown): Promise<string> {
  const key = `sha256:${await sha256Hex(stableStringify(body))}`;
  try { sessionStorage.setItem(IDEM_KEY_SS, key); } catch {}
  return key;
}

export function idempotencyKeyFromSession(): string | undefined {
  try { return sessionStorage.getItem(IDEM_KEY_SS) || undefined; } catch { return undefined; }
}