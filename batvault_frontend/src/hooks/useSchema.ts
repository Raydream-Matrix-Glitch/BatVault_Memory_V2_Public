import { useEffect, useState } from "react";
import type { SchemaFields, SchemaRelation } from "../types/memory";

// Module-level caches to avoid refetching schema repeatedly. These are
// intentionally unexported so that components can share the same data across
// renders and component lifecycles.
let cachedFields: SchemaFields | null = null;
let cachedRelations: SchemaRelation[] | null = null;
let cacheError: any = null;

/**
 * React hook to load schema fields and relations from the Memory API. It
 * caches responses to minimise network traffic and returns loading and error
 * state for the initial request. On subsequent mounts, cached values are
 * returned synchronously.
 */
export function useSchema() {
  const [fields, setFields] = useState<SchemaFields | null>(cachedFields);
  const [relations, setRelations] = useState<SchemaRelation[] | null>(
    cachedRelations
  );
  const [loading, setLoading] = useState(!cachedFields || !cachedRelations);
  const [error, setError] = useState<any>(cacheError);

  useEffect(() => {
    // If both fields and relations are cached, do nothing.
    if (cachedFields && cachedRelations) return;
    const abort = new AbortController();
    const fetchSchema = async () => {
      try {
        setLoading(true);
        setError(null);
        // Determine the base path for schema endpoints. Prefer VITE_API_BASE
        // from env variables; otherwise fall back to the window origin. In
        // test/JSDOM environments where window is undefined, default to
        // relative paths by using the empty string. Trim any trailing slash.
        const base = (() => {
          const envBase = (import.meta as any).env?.VITE_API_BASE as string | undefined;
          if (envBase) return envBase.replace(/\/$/, "");
          if (typeof window !== "undefined" && window.location) {
            return window.location.origin.replace(/\/$/, "");
          }
          return "";
        })();
        // Acquire bearer token from localStorage if present
        let token: string | undefined;
        try {
          token = localStorage.getItem("access_token") || undefined;
        } catch {
          token = undefined;
        }
        const headers: Record<string, string> = {
          Accept: "application/json",
        };
        if (token) headers["Authorization"] = `Bearer ${token}`;
        const [fieldsRes, relsRes] = await Promise.all([
          fetch(`${base}/v2/schema/fields`, { headers, signal: abort.signal }),
          fetch(`${base}/v2/schema/rels`, { headers, signal: abort.signal }),
        ]);
        if (!fieldsRes.ok || !relsRes.ok) {
          throw new Error(
            `Failed to fetch schema: ${fieldsRes.status}/${relsRes.status}`
          );
        }
        const fieldsJson = await fieldsRes.json();
        const relsJson = await relsRes.json();
        cachedFields = fieldsJson.fields as SchemaFields;
        cachedRelations = relsJson.relations as SchemaRelation[];
        cacheError = null;
        setFields(cachedFields);
        setRelations(cachedRelations);
        setLoading(false);
      } catch (err: any) {
        // Record error in cache so subsequent calls do not retry automatically
        cacheError = err;
        setError(err);
        setLoading(false);
      }
    };
    fetchSchema();
    return () => {
      abort.abort();
    };
  }, []);

  return { fields, relations, loading, error };
}