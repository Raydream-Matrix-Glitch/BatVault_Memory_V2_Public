// src/utils/sse.ts
export type OnSSELine = (payload: string) => void;

/**
 * Read the fetch Response stream line-by-line, invoking `onData` for each `data:` JSON payload.
 */
export async function readSSE(res: Response, onData: OnSSELine) {
  if (!res.body) throw new Error("No body in SSE response");
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");

  let buf = "";
  let doneFlag = false;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trimEnd();
      buf = buf.slice(nl + 1);

      if (!line) continue;
      if (line.startsWith(":")) continue; // SSE comment / keepalive

      if (line.startsWith("data:")) {
        const payload = line.slice("data:".length).trim();
        if (payload === "[DONE]") {
          // propagate synthetic “done” event as JSON so downstream never tries to JSON.parse("[DONE]")
          onData('{"event":"done"}');
          doneFlag = true;
          break;
        }
        if (payload) onData(payload);
      }
    }
  }

  // flush remainder when fetch stream ends without trailing NL
  if (buf && !doneFlag) {
    const line = buf.trimEnd();
    if (line.startsWith("data:")) onData(line.slice("data:".length).trim());
  }
}
