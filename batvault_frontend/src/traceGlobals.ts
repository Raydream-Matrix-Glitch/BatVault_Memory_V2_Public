/**
 * Keep the global helper that the backend calls (`setCurrentTrace(...)`)
 * in the production bundle.  If the real drawer isn’t wired yet we simply
 * no‑op, but the symbol *must* exist before the first /ask chunk arrives.
 */

declare global {
  interface Window {
    /**
     * Record the current trace metadata emitted when a stream completes.
     * The object contains selected audit fields such as request ID,
     * prompt fingerprint and snapshot ETag. Consumers of this function
     * should gracefully handle missing properties.
     */
    setCurrentTrace: (trace: {
      request_id?: string;
      prompt_fingerprint?: string;
      snapshot_etag?: string;
    }) => void;
  }
}

if (
  typeof window !== "undefined" &&
  typeof window.setCurrentTrace !== "function"
) {
  window.setCurrentTrace = () => {
    /* placeholder until trace drawer mounts */
  };
}

export {}; // keep TypeScript happy