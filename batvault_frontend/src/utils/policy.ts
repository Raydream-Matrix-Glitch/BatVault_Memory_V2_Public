import { DEFAULT_ROLE } from "./roles";
// NOTE: sensitivity ceiling is server-derived; FE must not set it.

// Fallback 32-hex generator (for X-Trace-Id / default X-Request-Id)
function randomHex32(): string {
  const arr = new Uint8Array(16);
  if (typeof globalThis !== "undefined" && globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(arr);
  } else {
    for (let i=0;i<arr.length;i++) arr[i] = Math.floor(Math.random()*256);
  }
  return Array.from(arr).map(b=>b.toString(16).padStart(2, "0")).join("");
}
// Accept a caller-supplied requestId (UUID v4 or 32-hex) so UI can reuse it across calls.
export function buildPolicyHeaders(reqId?: string, traceId?: string): Record<string, string> {
  const w: any = window as any;
  const role = String(w.BV_ACTIVE_ROLE || DEFAULT_ROLE).toLowerCase();
  const user = w.BV_USER_ID || "demo-user";
  const version = w.BV_POLICY_VERSION || "v3";
  const keyRaw = String(
    w.BV_POLICY_KEY ||
    (import.meta as any).env?.VITE_POLICY_KEY ||
    ""
  );
  const key = keyRaw.length > 0 ? keyRaw : "probe";
  // Sensitivity ceiling: explicit override > role mapping
  const sensOverride = String(
    w.BV_SENSITIVITY_CEILING ||
    (import.meta as any).env?.VITE_SENSITIVITY_CEILING ||
    ""
  ).trim().toLowerCase();
  const ROLE_CEILING: Record<string, string> = { analyst: "low", manager: "medium", ceo: "high" };
  const ceiling = sensOverride || ROLE_CEILING[(role as keyof typeof ROLE_CEILING)] || "low";

  // Prefer the caller-provided reqId (UUID or 32-hex). Fallback to stable 32-hex.
  const rid = (reqId && String(reqId).trim()) || randomHex32();
  const tid = (traceId && String(traceId).trim()) || randomHex32();

  return {
    "X-User-Id": user,
    "X-User-Roles": role,
    "X-Policy-Version": version,
    "X-Policy-Key": key,
    "X-Request-Id": rid,
    "X-Trace-Id": tid,
  };
}