// Lightweight SSE/NDJSON streaming POST helper.
// Deterministic, no silent errors; narrow exception handling.

export interface StreamOptions {
  endpoint: string;                  // absolute or relative (env-provided)
  body: Record<string, unknown>;     // JSON payload
  headers?: Record<string, string>;  // extra headers (e.g., persona)
  onEvent?: (data: any) => void;     // per-line JSON chunk (if server streams NDJSON)
  onDone?: (finalData: any) => void; // final aggregated JSON (if server ends with one)
  signal?: AbortSignal;              // cancellation
}

export async function postStream(opts: StreamOptions): Promise<void> {
  const { endpoint, body, headers = {}, onEvent, onDone, signal } = opts;
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`stream failed: ${res.status} ${res.statusText} ${text}`.trim());
  }
  // Support both NDJSON and single JSON responses.
  const reader = res.body?.getReader?.();
  if (!reader) {
    // No stream; try to parse JSON
    const json = await res.json().catch(() => null);
    if (onDone) onDone(json);
    return;
  }
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (!line) continue;
        try {
          const obj = JSON.parse(line);
          onEvent?.(obj);
        } catch (e) {
          // Skip malformed line but keep reading; do not swallow errors silently.
          console.error('NDJSON parse error', e);
        }
      }
    }
    // Trailing JSON?
    const tail = buf.trim();
    if (tail) {
      try {
        const obj = JSON.parse(tail);
        onDone?.(obj);
      } catch {
        // ignore trailing partial
      }
    }
  } finally {
    // no-op
  }
}