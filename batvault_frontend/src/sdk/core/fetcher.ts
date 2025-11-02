/**
 * Fetcher for Orval-generated clients.
 * Centralizes headers (traceparent, policy) and base URL via request init.
 */
import { buildPolicyHeaders } from '../../utils/policy';
import { publishTraceIds } from '../../traceGlobals';

function hex(n: number) { return crypto.getRandomValues(new Uint8Array(n)).reduce((s, b) => s + b.toString(16).padStart(2,'0'), ''); }
function makeTraceparent(traceId?: string): string {
  const tid = (traceId && /^[0-9a-f]{32}$/i.test(traceId)) ? traceId : hex(16);
  const pid = hex(8);
  return `00-${tid}-${pid}-01`;
}

export const makeFetcher = () => async <T>(url: string, init?: RequestInit): Promise<T> => {
  const traceparent = makeTraceparent();
  const headers = {
    'Content-Type': 'application/json',
    'traceparent': traceparent,
    ...buildPolicyHeaders(),
    ...(init?.headers || {}),
  } as Record<string, string>;
  const res = await fetch(url, { ...init, headers });
  publishTraceIds({ traceparent });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} ${res.statusText} :: ${text}`);
  }
  return res.json() as Promise<T>;
};
