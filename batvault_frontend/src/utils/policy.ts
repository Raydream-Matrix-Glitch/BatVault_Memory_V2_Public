import { DEFAULT_ROLE } from "./roles";
export function buildPolicyHeaders(reqId?: string, traceId?: string): Record<string, string> {
  const w: any = window as any;
  const role = (w.BV_ACTIVE_ROLE || DEFAULT_ROLE).toLowerCase();
  const user = w.BV_USER_ID || "demo-user";
  const version = w.BV_POLICY_VERSION || "v3-demo";
  const keyRaw = String(
      w.BV_POLICY_KEY ||
        (import.meta as any).env?.VITE_POLICY_KEY ||
        ""
    ).trim();
  // Always send a non-empty policy key so the backend can respond with
  // a policy_key_mismatch (from which we learn the computed fingerprint).
  const key = keyRaw.length > 0 ? keyRaw : "probe";

  // Deterministic 32-hex request/trace ids (fallback to random)
  function randomHex32(): string {
    try {
      const a = crypto.getRandomValues(new Uint8Array(16));
      return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
    } catch {
      let out = "";
      for (let i = 0; i < 32; i++) out += Math.floor(Math.random() * 16).toString(16);
      return out;
    }
  }
  const rid = reqId || randomHex32();
  const tid = traceId || randomHex32();

  return {
    "X-User-Id": user,
    "X-User-Roles": role,
    "X-Policy-Version": version,
    "X-Policy-Key": key,
    "X-Request-Id": rid,
    "X-Trace-Id": tid,
  };
}