export type paths = {
    "/api/enrich": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Enrich
         * @description Type-agnostic enrich: lookup by anchor (Decision, Event, future types).
         *     Snapshot policy: STRICT — requires X-Snapshot-ETag matching the current snapshot; missing/mismatch → 412.
         *     Headers: mirrors x-snapshot-etag and X-BV-Policy-Fingerprint; does NOT set X-BV-Graph-FP (no graph in the payload).
         *     Contract: returns a single masked node plus mask_summary.
         */
        get: operations["enrich_api_enrich_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        /**
         * Enrich Head
         * @description Cheap freshness check: returns only ETag (quoted) and x-snapshot-etag; no body.
         *     No snapshot precondition required. Use If-None-Match with the quoted ETag to get 304.
         */
        head: operations["enrich_head_api_enrich_head"];
        patch?: never;
        trace?: never;
    };
    "/api/enrich/batch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Enrich Batch
         * @description Enrich a bounded set of node IDs for short-answer composition.
         *     Contract (Baseline v3, snapshot-bound):
         *       - Input: {"anchor_id": "<domain#id>", "snapshot_etag": "<etag>", "ids": ["..."]}
         *       - Policy: honour the same policy headers as /api/enrich; ACL-guard each item.
         *       - Scope safety: Memory recomputes the authorized set from the snapshot_etag/policy and
         *         **denies the whole call** if `requested_ids ⊄ allowed_ids` (default 403; optional 404 via x-denied-status).
         *       - Precondition: snapshot_etag is REQUIRED (body or X-Snapshot-ETag); missing/mismatch → 412.
         *       - Output on success: {"items": {"<id>": {...masked enriched node...}, ...}} with minimal meta.
         */
        post: operations["enrich_batch_api_enrich_batch_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/enrich/event": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Enrich Event
         * @description Event-only enrich by anchor.
         *     Snapshot policy: STRICT — same as /api/enrich (requires X-Snapshot-ETag).
         *     Headers: mirrors x-snapshot-etag and X-BV-Policy-Fingerprint; does NOT set X-BV-Graph-FP.
         */
        get: operations["enrich_event_api_enrich_event_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/graph/expand_candidates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Expand Candidates
         * @description Edges-only graph view around the anchor (k=1).
         *     Snapshot policy: STRICT — requires X-Snapshot-ETag (or `snapshot_etag` in body); missing/mismatch → 412. Mirrors x-snapshot-etag in responses for cache keys.
         *     Headers: sets X-BV-Graph-FP and mirrors X-BV-Policy-Fingerprint (graph fingerprint also present at meta.fingerprints.graph_fp).
         *     Contract: meta.fingerprints ONLY contains graph_fp; bundle_fp is never present here.
         */
        post: operations["expand_candidates_api_graph_expand_candidates_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/resolve/text": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resolve Text */
        post: operations["resolve_text_api_resolve_text_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/healthz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Healthz */
        get: operations["_healthz_healthz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/readyz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Readyz */
        get: operations["_readyz_readyz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
};
export type webhooks = Record<string, never>;
export type components = {
    schemas: {
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
};
export type $defs = Record<string, never>;
export interface operations {
    enrich_api_enrich_get: {
        parameters: {
            query: {
                anchor: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    enrich_head_api_enrich_head: {
        parameters: {
            query: {
                anchor: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    enrich_batch_api_enrich_batch_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    enrich_event_api_enrich_event_get: {
        parameters: {
            query: {
                anchor: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    expand_candidates_api_graph_expand_candidates_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resolve_text_api_resolve_text_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    _healthz_healthz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    _readyz_readyz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
}
