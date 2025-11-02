export type paths = {
    "/config": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Public Config */
        get: operations["get_public_config_config_get"];
        put?: never;
        post?: never;
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
    "/memory/{path}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Proxy Memory
         * @description Thin pass-through so the browser can call Memory via the Gateway origin.
         *     Uses the shared AsyncClient from core_http.client.
         */
        get: operations["proxy_memory_memory__path__delete_get"];
        /**
         * Proxy Memory
         * @description Thin pass-through so the browser can call Memory via the Gateway origin.
         *     Uses the shared AsyncClient from core_http.client.
         */
        put: operations["proxy_memory_memory__path__delete_put"];
        /**
         * Proxy Memory
         * @description Thin pass-through so the browser can call Memory via the Gateway origin.
         *     Uses the shared AsyncClient from core_http.client.
         */
        post: operations["proxy_memory_memory__path__delete_post"];
        /**
         * Proxy Memory
         * @description Thin pass-through so the browser can call Memory via the Gateway origin.
         *     Uses the shared AsyncClient from core_http.client.
         */
        delete: operations["proxy_memory_memory__path__delete"];
        /**
         * Proxy Memory
         * @description Thin pass-through so the browser can call Memory via the Gateway origin.
         *     Uses the shared AsyncClient from core_http.client.
         */
        options: operations["proxy_memory_memory__path__delete_options"];
        /**
         * Proxy Memory
         * @description Thin pass-through so the browser can call Memory via the Gateway origin.
         *     Uses the shared AsyncClient from core_http.client.
         */
        head: operations["proxy_memory_memory__path__delete_head"];
        /**
         * Proxy Memory
         * @description Thin pass-through so the browser can call Memory via the Gateway origin.
         *     Uses the shared AsyncClient from core_http.client.
         */
        patch: operations["proxy_memory_memory__path__delete_patch"];
        trace?: never;
    };
    "/ops/minio/ensure-bucket": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Ensure Bucket */
        post: operations["ensure_bucket_ops_minio_ensure_bucket_post"];
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
    "/v3/ops/minio/config": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ops Minio Config
         * @description Diagnostic: expose the *effective* MinIO settings this process is using.
         *     Secrets are not returned.
         */
        get: operations["ops_minio_config_v3_ops_minio_config_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v3/ops/minio/head/{request_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ops Minio Head
         * @description Diagnostic: stat an object for a given request_id.
         *     Now supports *both*:
         *       - archive names:  bundle_view / bundle_full  →  <rid>/<name>.tar.gz
         *       - artifact names: receipt.json / response.json / trace.json / bundle.manifest.json → <rid>/<name>
         */
        get: operations["ops_minio_head_v3_ops_minio_head__request_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v3/ops/minio/ls/{request_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ops Minio Ls
         * @description Diagnostic: list objects under a prefix (defaults to f"{request_id}/").
         *     Same-origin; avoids CORS. Helps verify bucket/endpoint/prefix alignment.
         */
        get: operations["ops_minio_ls_v3_ops_minio_ls__request_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v3/query": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** V3 Query */
        post: operations["v3_query_v3_query_post"];
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
        /** AnswerBlocks */
        AnswerBlocks: {
            /** Decision Id */
            decision_id?: string | null;
            /** Description */
            description?: string | null;
            /** Key Events */
            key_events?: string[] | null;
            /** Lead */
            lead: string;
            /** Next */
            next?: string | null;
            owner?: components["schemas"]["AnswerOwner"] | null;
        };
        /** AnswerOwner */
        AnswerOwner: {
            /** Name */
            name: string;
            /** Role */
            role?: string | null;
        };
        /** CompletenessFlags */
        CompletenessFlags: {
            [key: string]: unknown;
        };
        /** GraphEdgesModel */
        GraphEdgesModel: {
            /** Edges */
            edges?: {
                [key: string]: unknown;
            }[];
        } & {
            [key: string]: unknown;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** MemoryMetaModel */
        MemoryMetaModel: {
            /** Allowed Ids Fp */
            allowed_ids_fp?: string | null;
            /** Policy Fp */
            policy_fp?: string | null;
            /** Snapshot Etag */
            snapshot_etag?: string | null;
        } & {
            [key: string]: unknown;
        };
        /** QueryRequest */
        QueryRequest: {
            /** Anchor */
            anchor?: string | null;
            /** Graph */
            graph?: {
                [key: string]: unknown;
            } | null;
            /** Policy */
            policy?: {
                [key: string]: unknown;
            } | null;
            /** Question */
            question: string;
        } & {
            [key: string]: unknown;
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
        /** WhyDecisionAnchor */
        WhyDecisionAnchor: {
            [key: string]: unknown;
        };
        /** WhyDecisionAnswer */
        "WhyDecisionAnswer-Input": {
            blocks: components["schemas"]["AnswerBlocks"];
        } & {
            [key: string]: unknown;
        };
        /** WhyDecisionAnswer */
        "WhyDecisionAnswer-Output": {
            blocks: components["schemas"]["AnswerBlocks"];
        } & {
            [key: string]: unknown;
        };
        /** WhyDecisionResponse */
        WhyDecisionResponse: {
            anchor: components["schemas"]["WhyDecisionAnchor"];
            answer: components["schemas"]["WhyDecisionAnswer-Output"];
            completeness_flags?: components["schemas"]["CompletenessFlags"] | null;
            graph: components["schemas"]["GraphEdgesModel"];
            meta: components["schemas"]["MemoryMetaModel"];
        } & {
            [key: string]: unknown;
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
    get_public_config_config_get: {
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
                    "application/json": {
                        [key: string]: unknown;
                    };
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
    proxy_memory_memory__path__delete_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                path: string;
            };
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
    proxy_memory_memory__path__delete_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                path: string;
            };
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
    proxy_memory_memory__path__delete_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                path: string;
            };
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
    proxy_memory_memory__path__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                path: string;
            };
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
    proxy_memory_memory__path__delete_options: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                path: string;
            };
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
    proxy_memory_memory__path__delete_head: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                path: string;
            };
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
    proxy_memory_memory__path__delete_patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                path: string;
            };
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
    ensure_bucket_ops_minio_ensure_bucket_post: {
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
    ops_minio_config_v3_ops_minio_config_get: {
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
    ops_minio_head_v3_ops_minio_head__request_id__get: {
        parameters: {
            query?: {
                name?: string;
            };
            header?: never;
            path: {
                request_id: string;
            };
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
    ops_minio_ls_v3_ops_minio_ls__request_id__get: {
        parameters: {
            query?: {
                prefix?: string | null;
                limit?: number;
            };
            header?: never;
            path: {
                request_id: string;
            };
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
    v3_query_v3_query_post: {
        parameters: {
            query?: {
                stream?: boolean;
                include_event?: boolean;
                fresh?: boolean;
                template?: string | null;
                org?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QueryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WhyDecisionResponse"];
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
}
