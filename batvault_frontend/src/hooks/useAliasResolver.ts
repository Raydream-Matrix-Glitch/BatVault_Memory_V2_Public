import { useRef, useCallback } from "react";
import { logEvent } from "../utils/logger";

/**
 * Hook to resolve a decision slug (e.g. `panasonic-exit-plasma-2012`) to a
 * human‑friendly title. It caches previous lookups and honours ETag headers
 * returned by the backend. When the slug exists in the local cache a
 * `ui_memory_alias_cache_hit` event is emitted, otherwise a
 * `ui_memory_alias_cache_miss` event is emitted. Cache entries are keyed
 * by slug and store both the resolved title and the associated ETag (if
 * provided). Subsequent requests include the `If‑None‑Match` header to
 * support conditional GETs.
 */
export function useAliasResolver() {
  // Simple in‑memory cache mapping slug → {title, etag}. This persists for
  // the lifetime of the hook and avoids repeated network calls for the same
  // slug.
  const cacheRef = useRef<Map<string, { title?: string; etag?: string }>>(new Map());

  // Determine API base: honour Vite's VITE_API_BASE env if present. If
  // running in a browser, default to the current origin; otherwise use
  // relative paths (empty string).
  const base: string = (() => {
    const envBase = (import.meta as any).env?.VITE_API_BASE as string | undefined;
    if (envBase) return envBase.replace(/\/$/, "");
    if (typeof window !== "undefined" && window.location) {
      return window.location.origin.replace(/\/$/, "");
    }
    return "";
  })();

  /**
   * Resolve the given slug to a human title. Returns `undefined` if no
   * title can be determined. Network errors are silently ignored. Calls
   * emit cache hit/miss events for observability.
   */
  const resolveAlias = useCallback(
    async (slug: string | undefined | null): Promise<string | undefined> => {
      if (!slug) return undefined;
      // Check the cache first
      const existing = cacheRef.current.get(slug);
      if (existing && existing.title) {
        try {
          logEvent("ui_memory_alias_cache_hit", { id: slug });
        } catch {
          /* ignore logging errors */
        }
        return existing.title;
      }
      // Not found in cache: emit miss
      try {
        logEvent("ui_memory_alias_cache_miss", { id: slug });
      } catch {
        /* ignore logging errors */
      }
      // Build fetch options including If‑None‑Match if we have a previous etag
      const headers: Record<string, string> = {};
      if (existing && existing.etag) {
        headers["If-None-Match"] = existing.etag;
      }
      try {
        const url = `${base}/api/enrich/decision/${encodeURIComponent(slug)}`;
        const resp = await fetch(url, { headers });
        // If the server returns 304 Not Modified, reuse cached value
        if (resp.status === 304) {
          return existing?.title;
        }
        if (resp.ok) {
          const data = await resp.json().catch(() => ({}));
          // The enriched decision payload exposes the human option on the
          // `option` field; fall back to the id if absent.
          const record = data && data.option ? data : data.data || {};
          const title: string | undefined =
            record.option || record.option || record.id;
          // Store the ETag for future conditional requests
          const etag = resp.headers.get("etag") || resp.headers.get("ETag") || undefined;
          cacheRef.current.set(slug, { title, etag });
          return title;
        }
      } catch {
        /* network errors ignored; do not throw */
      }
      return undefined;
    },
    [base]
  );

  return resolveAlias;
  }