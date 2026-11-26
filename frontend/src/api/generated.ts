// Generated from the actual FastAPI OpenAPI schema. Run make generate-api.
export interface paths {
    "/api/cache": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Clear Cache */
        delete: operations["clear_cache_api_cache_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/catalog": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Catalog */
        get: operations["catalog_api_catalog_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/chat": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Chat */
        post: operations["chat_api_chat_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/chat/{request_id}/approval": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approval */
        post: operations["approval_api_chat__request_id__approval_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cloud": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Cloud Destinations */
        get: operations["cloud_destinations_api_cloud_get"];
        put?: never;
        /**
         * Add Cloud Destination
         * @description Register a new OpenAI-compatible destination.
         *
         *     Adding one widens where sanitized text may be sent, so it needs the local
         *     write token and the same https/host validation the packaged entries get.
         *     Nothing is sent anywhere until a key is supplied and the mode allows cloud.
         */
        post: operations["add_cloud_destination_api_cloud_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cloud/{model_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Remove Cloud Destination */
        delete: operations["remove_cloud_destination_api_cloud__model_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cloud/{model_id}/key": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Cloud Key */
        put: operations["set_cloud_key_api_cloud__model_id__key_put"];
        post?: never;
        /** Clear Cloud Key */
        delete: operations["clear_cloud_key_api_cloud__model_id__key_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cloud/{model_id}/model": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Cloud Model */
        put: operations["set_cloud_model_api_cloud__model_id__model_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cloud/{model_id}/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Cloud Models
         * @description Ask the destination which models this key can reach.
         *
         *     Reaches the owner-declared destination carrying only the key, so it is a
         *     write-authorized route even though it reads: it spends the credential.
         */
        get: operations["list_cloud_models_api_cloud__model_id__models_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cloud/presets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Cloud Presets
         * @description Known providers the owner can add without typing an endpoint.
         *
         *     Already-registered providers are omitted so the picker cannot offer a
         *     duplicate that POST /api/cloud would then refuse.
         */
        get: operations["cloud_presets_api_cloud_presets_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Conversations */
        get: operations["list_conversations_api_conversations_get"];
        /** Save Conversation */
        put: operations["save_conversation_api_conversations_put"];
        post?: never;
        /** Delete All Conversations */
        delete: operations["delete_all_conversations_api_conversations_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/conversations/{conversation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Conversation */
        get: operations["read_conversation_api_conversations__conversation_id__get"];
        put?: never;
        post?: never;
        /** Delete Conversation */
        delete: operations["delete_conversation_api_conversations__conversation_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health Status */
        get: operations["health_status_api_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/metrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Metrics Status */
        get: operations["metrics_status_api_metrics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/preferences": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Preferences */
        get: operations["get_preferences_api_preferences_get"];
        /** Update Preferences */
        put: operations["update_preferences_api_preferences_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/preferences/reset": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reset Preferences */
        post: operations["reset_preferences_api_preferences_reset_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/telemetry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Telemetry Summary */
        get: operations["telemetry_summary_api_telemetry_get"];
        put?: never;
        post?: never;
        /** Clear Telemetry */
        delete: operations["clear_telemetry_api_telemetry_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** ApprovalRequest */
        ApprovalRequest: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "approve" | "decline" | "cancel";
            /** Digest */
            digest?: string | null;
            /**
             * Proposal Id
             * Format: uuid
             */
            proposal_id: string;
            /** Sanitized Preview */
            sanitized_preview?: string | null;
        };
        /** ApprovalRequiredResponse */
        ApprovalRequiredResponse: {
            /** Best Local Answer */
            best_local_answer?: string | null;
            /** Digest */
            digest?: string | null;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** @default ask_before_cloud */
            mode?: components["schemas"]["ExecutionMode"];
            /** Model Id */
            model_id?: string | null;
            /** Model Version */
            model_version?: string | null;
            /** Policy Version */
            policy_version?: string | null;
            /** Proposal Id */
            proposal_id?: string | null;
            reason: components["schemas"]["CloudFallbackReason"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /** Sanitized Preview */
            sanitized_preview: string;
            /**
             * Status
             * @default approval_required
             * @constant
             */
            status?: "approval_required";
        };
        /** CacheClearedResponse */
        CacheClearedResponse: {
            /** Deleted */
            deleted: number;
        };
        /** CacheHitRate */
        CacheHitRate: {
            /** Cache Hits */
            cache_hits: number;
            /** Hit Rate */
            hit_rate: number | null;
            /** Total Records */
            total_records: number;
        };
        /** CacheProvenance */
        CacheProvenance: {
            /** Model Id */
            model_id: string;
            /** Model Version */
            model_version: string;
            /** Policy Version */
            policy_version: string;
            /** Schema Version */
            schema_version: string;
            /**
             * Source
             * @default local
             * @constant
             */
            source?: "local";
        };
        /**
         * CacheStatus
         * @enum {string}
         */
        CacheStatus: "disabled" | "bypass" | "miss" | "exact_hit" | "semantic_hit";
        /** CancelledResponse */
        CancelledResponse: {
            /**
             * Reason
             * @enum {string}
             */
            reason: "decline" | "cancel";
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Status
             * @default cancelled
             * @constant
             */
            status?: "cancelled";
        };
        /** CatalogModel */
        CatalogModel: {
            /**
             * Eligible
             * @default false
             */
            eligible?: boolean;
            /** Family */
            family: string;
            /** Installed */
            installed: boolean;
            /** Manifest Version */
            manifest_version: string;
            /** Model Id */
            model_id: string;
            /** Quantization */
            quantization: string;
            /** Resident */
            resident?: boolean | null;
            /** Verified */
            verified: boolean;
        };
        /** CatalogResponse */
        CatalogResponse: {
            /**
             * Hardware Known
             * @default false
             */
            hardware_known?: boolean;
            /**
             * Mock Mode
             * @default false
             */
            mock_mode?: boolean;
            /**
             * Models
             * @default []
             */
            models?: components["schemas"]["CatalogModel"][];
        };
        /** ChatRequest */
        ChatRequest: {
            constraints?: components["schemas"]["ResponseConstraints"];
            /**
             * History
             * @default []
             */
            history?: components["schemas"]["ConversationTurn"][];
            /** @default local_only */
            mode?: components["schemas"]["ExecutionMode"];
            options?: components["schemas"]["GenerationOptions"];
            /** Prompt */
            prompt: string;
            /** @default unspecified */
            sensitivity_scope?: components["schemas"]["SensitivityScope"];
            task_hint?: components["schemas"]["TaskCategory"] | null;
        };
        /** ChatResponse */
        ChatResponse: {
            /** Answer */
            answer: string;
            cache_provenance?: components["schemas"]["CacheProvenance"] | null;
            /** @default disabled */
            cache_status?: components["schemas"]["CacheStatus"];
            /** Estimated Cost Usd */
            estimated_cost_usd?: number | null;
            generation_metrics?: components["schemas"]["GenerationMetrics"] | null;
            /** Manifest Version */
            manifest_version?: string | null;
            /**
             * Mock
             * @default false
             */
            mock?: boolean;
            /** Model Id */
            model_id: string;
            /** Model Version */
            model_version: string;
            /**
             * Privacy Counts
             * @default []
             */
            privacy_counts?: components["schemas"]["PrivacyTypeCount"][];
            /** Reason */
            reason: string;
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Restored
             * @default false
             */
            restored?: boolean;
            /**
             * Route
             * @enum {string}
             */
            route: "local" | "cloud";
            /**
             * Router Type
             * @default rules
             * @enum {string}
             */
            router_type?: "rules" | "lightgbm";
            /** Router Version */
            router_version?: string | null;
            /**
             * Status
             * @default success
             * @constant
             */
            status?: "success";
            task?: components["schemas"]["TaskCategory"] | null;
            timings: components["schemas"]["ResponseTimings"];
        };
        /** CloudApiKeyUpdate */
        CloudApiKeyUpdate: {
            /** Api Key */
            api_key: string;
        };
        /** CloudDestinationCreate */
        CloudDestinationCreate: {
            /** Display Name */
            display_name: string;
            /** Endpoint */
            endpoint: string;
            /** Health Endpoint */
            health_endpoint?: string | null;
            /** Model Id */
            model_id: string;
        };
        /** CloudDestinationInfo */
        CloudDestinationInfo: {
            /** Display Name */
            display_name: string;
            /** Endpoint */
            endpoint: string;
            /** Key Available */
            key_available: boolean;
            /**
             * Key Source
             * @enum {string}
             */
            key_source: "env" | "runtime" | "none";
            /** Model Id */
            model_id: string;
            /** Model Version */
            model_version: string;
            /** Runtime Model */
            runtime_model?: string | null;
        };
        /** CloudDestinationsResponse */
        CloudDestinationsResponse: {
            /**
             * Destinations
             * @default []
             */
            destinations?: components["schemas"]["CloudDestinationInfo"][];
        };
        /**
         * CloudFallbackReason
         * @enum {string}
         */
        CloudFallbackReason: "local_error" | "validation_failure" | "no_local_model" | "cloud_preferred" | "cloud_only";
        /** CloudModelOptions */
        CloudModelOptions: {
            /** Model Id */
            model_id: string;
            /**
             * Models
             * @default []
             */
            models?: string[];
            /**
             * Reachable
             * @default false
             */
            reachable?: boolean;
            /** Runtime Model */
            runtime_model?: string | null;
        };
        /** CloudModelUpdate */
        CloudModelUpdate: {
            /** Runtime Model */
            runtime_model: string;
        };
        /** CloudPresetInfo */
        CloudPresetInfo: {
            /** Display Name */
            display_name: string;
            /** Endpoint */
            endpoint: string;
            /** Health Endpoint */
            health_endpoint: string;
            /** Model Id */
            model_id: string;
            /**
             * Note
             * @default
             */
            note?: string;
        };
        /** CloudPresetsResponse */
        CloudPresetsResponse: {
            /**
             * Presets
             * @default []
             */
            presets?: components["schemas"]["CloudPresetInfo"][];
        };
        /** ConversationDetail */
        ConversationDetail: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Messages
             * @default []
             */
            messages?: components["schemas"]["ConversationMessage"][];
        };
        /** ConversationListResponse */
        ConversationListResponse: {
            /**
             * Conversations
             * @default []
             */
            conversations?: components["schemas"]["ConversationSummary"][];
            /**
             * Enabled
             * @default false
             */
            enabled?: boolean;
        };
        /** ConversationMessage */
        ConversationMessage: {
            /** Content */
            content: string;
            /** Model Id */
            model_id?: string | null;
            /**
             * Role
             * @enum {string}
             */
            role: "user" | "assistant";
        };
        /** ConversationSaveRequest */
        ConversationSaveRequest: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Messages */
            messages: components["schemas"]["ConversationMessage"][];
        };
        /** ConversationSummary */
        ConversationSummary: {
            /** Created At */
            created_at: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Message Count */
            message_count: number;
            /** Title */
            title: string;
            /** Updated At */
            updated_at: number;
        };
        /** ConversationTurn */
        ConversationTurn: {
            /** Content */
            content: string;
            /**
             * Role
             * @enum {string}
             */
            role: "user" | "assistant";
        };
        /**
         * ErrorCode
         * @enum {string}
         */
        ErrorCode: "unavailable" | "timeout" | "policy_blocked" | "approval_conflict" | "insufficient_disk" | "internal" | "invalid_request" | "invalid_config" | "validation_failed" | "privacy_failed" | "output_blocked";
        /** ErrorResponse */
        ErrorResponse: {
            /** Answer */
            answer?: "[Response withheld: privacy inspection could not confirm safe output.]" | null;
            error_code: components["schemas"]["ErrorCode"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Retryable
             * @default false
             */
            retryable?: boolean;
            /**
             * Status
             * @default error
             * @constant
             */
            status?: "error";
        };
        /**
         * ExecutionMode
         * @enum {string}
         */
        ExecutionMode: "local_only" | "ask_before_cloud" | "auto_cloud" | "cloud_only";
        /** GenerationMetrics */
        GenerationMetrics: {
            /** Generation Ms */
            generation_ms?: number | null;
            /** Input Tokens */
            input_tokens?: number | null;
            /** Load Ms */
            load_ms?: number | null;
            /** Output Tokens */
            output_tokens?: number | null;
            /** Output Tokens Per Second */
            output_tokens_per_second?: number | null;
            /** Prompt Ms */
            prompt_ms?: number | null;
            /** Runtime Total Ms */
            runtime_total_ms?: number | null;
        };
        /** GenerationOptions */
        GenerationOptions: {
            /**
             * Max Output Tokens
             * @default 1024
             */
            max_output_tokens?: number;
            /** Seed */
            seed?: number | null;
            /**
             * Temperature
             * @default 0
             */
            temperature?: number;
        };
        /** HealthResponse */
        HealthResponse: {
            /** Eligible Local Models */
            eligible_local_models: number;
            /**
             * Mode
             * @enum {string}
             */
            mode: "mock" | "injected" | "real";
            /** Notice */
            notice?: "ollama_unavailable_using_mock" | null;
            /** Privacy Analyzer */
            privacy_analyzer: string;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "degraded";
            /**
             * Worker Limit
             * @default 1
             * @constant
             */
            worker_limit?: 1;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** JsonField */
        JsonField: {
            kind: components["schemas"]["JsonType"];
            /** Name */
            name: string;
            /**
             * Required
             * @default true
             */
            required?: boolean;
        };
        /** JsonObjectSchema */
        JsonObjectSchema: {
            /**
             * Allow Extra
             * @default false
             */
            allow_extra?: boolean;
            /**
             * Fields
             * @default []
             */
            fields?: components["schemas"]["JsonField"][];
        };
        /**
         * JsonType
         * @enum {string}
         */
        JsonType: "object" | "array" | "string" | "number" | "integer" | "boolean" | "null";
        /** MetricsResponse */
        MetricsResponse: {
            /**
             * Cache Enabled
             * @default false
             */
            cache_enabled?: boolean;
            /**
             * Cache Entries
             * @default 0
             */
            cache_entries?: number;
            /**
             * Cache Hits
             * @default 0
             */
            cache_hits?: number;
            /** Completed */
            completed: number;
            /** Errors */
            errors: number;
            /** Estimated Cost Usd */
            estimated_cost_usd?: null;
            /**
             * Learned Router Enabled
             * @default false
             * @constant
             */
            learned_router_enabled?: false;
            /** Pending Approvals */
            pending_approvals: number;
            /**
             * Persistence Enabled
             * @default false
             * @constant
             */
            persistence_enabled?: false;
            /** Requests */
            requests: number;
            /**
             * Status
             * @default ephemeral_only
             * @constant
             */
            status?: "ephemeral_only";
        };
        /** ModelUsage */
        ModelUsage: {
            /** Count */
            count: number;
            /** Model Id */
            model_id: string;
            /** Model Version */
            model_version: string;
        };
        /** PerCategoryLatency */
        PerCategoryLatency: {
            /** Median Ms */
            median_ms: number | null;
            /** P90 Ms */
            p90_ms: number | null;
            /** Sample Count */
            sample_count: number;
            task: components["schemas"]["TaskCategory"];
        };
        /** PreferencesResponse */
        PreferencesResponse: {
            /**
             * Exact Cache Enabled
             * @default false
             */
            exact_cache_enabled?: boolean;
            /**
             * Local Learning Enabled
             * @default false
             */
            local_learning_enabled?: boolean;
            /** @default local_only */
            mode?: components["schemas"]["ExecutionMode"];
            /**
             * Persistent Analytics Enabled
             * @default false
             */
            persistent_analytics_enabled?: boolean;
            /**
             * Workload Persistence Enabled
             * @default false
             */
            workload_persistence_enabled?: boolean;
        };
        /** PreferencesUpdateRequest */
        PreferencesUpdateRequest: {
            /** Exact Cache Enabled */
            exact_cache_enabled?: boolean | null;
            /** Local Learning Enabled */
            local_learning_enabled?: boolean | null;
            /** Persistent Analytics Enabled */
            persistent_analytics_enabled?: boolean | null;
            /** Workload Persistence Enabled */
            workload_persistence_enabled?: boolean | null;
        };
        /** PrivacyTypeCount */
        PrivacyTypeCount: {
            /** Count */
            count: number;
            /** Entity Type */
            entity_type: string;
        };
        /** ResponseConstraints */
        ResponseConstraints: {
            /**
             * Allowed Labels
             * @default []
             */
            allowed_labels?: string[];
            json_schema?: components["schemas"]["JsonObjectSchema"] | null;
            /** Max Bullets */
            max_bullets?: number | null;
            /**
             * Max Chars
             * @default 64000
             */
            max_chars?: number;
            /** Min Bullets */
            min_bullets?: number | null;
            /**
             * Min Chars
             * @default 1
             */
            min_chars?: number;
            /**
             * Required Fields
             * @default []
             */
            required_fields?: string[];
            /** @default text */
            response_format?: components["schemas"]["ResponseFormat"];
        };
        /**
         * ResponseFormat
         * @enum {string}
         */
        ResponseFormat: "text" | "json";
        /** ResponseTimings */
        ResponseTimings: {
            /** Generation Ms */
            generation_ms?: number | null;
            /** Privacy Ms */
            privacy_ms?: number | null;
            /** Routing Ms */
            routing_ms?: number | null;
            /** Total Ms */
            total_ms: number;
        };
        /**
         * SensitivityScope
         * @enum {string}
         */
        SensitivityScope: "unspecified" | "non_sensitive" | "confidential";
        /**
         * TaskCategory
         * @enum {string}
         */
        TaskCategory: "qa" | "summarise" | "extract" | "classify" | "reason" | "format" | "code";
        /**
         * TelemetrySummary
         * @description Aggregate numbers suitable for a lightweight dashboard.
         *
         *     Statistics are *suppressed* (returned as ``None``) when the sample count is
         *     below a stability threshold so sparse local data cannot mislead the viewer.
         */
        TelemetrySummary: {
            cache: components["schemas"]["CacheHitRate"];
            /** Enabled */
            enabled: boolean;
            latency_overall: components["schemas"]["PerCategoryLatency"] | null;
            /** Latency Per Task */
            latency_per_task: components["schemas"]["PerCategoryLatency"][];
            /** Model Usage */
            model_usage: components["schemas"]["ModelUsage"][];
            /** Outcome Counts */
            outcome_counts: {
                [key: string]: number;
            };
            /** Reason Counts */
            reason_counts: {
                [key: string]: number;
            };
            /** Retention Days */
            retention_days: number;
            /** Route Counts */
            route_counts: {
                [key: string]: number;
            };
            /** Router Type Counts */
            router_type_counts: {
                [key: string]: number;
            };
            /** Sample Count */
            sample_count: number;
            /**
             * Schema Version
             * @default telemetry-summary-v1
             * @constant
             */
            schema_version?: "telemetry-summary-v1";
            /**
             * Suppression Threshold
             * @default 30
             */
            suppression_threshold?: number;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
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
}
export type $defs = Record<string, never>;
export interface operations {
    clear_cache_api_cache_delete: {
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
                    "application/json": components["schemas"]["CacheClearedResponse"];
                };
            };
        };
    };
    catalog_api_catalog_get: {
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
                    "application/json": components["schemas"]["CatalogResponse"];
                };
            };
        };
    };
    chat_api_chat_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChatRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChatResponse"] | components["schemas"]["ApprovalRequiredResponse"] | components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Accepted */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApprovalRequiredResponse"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal Server Error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Bad Gateway */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Gateway Timeout */
            504: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    approval_api_chat__request_id__approval_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApprovalRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChatResponse"] | components["schemas"]["CancelledResponse"] | components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Internal Server Error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Bad Gateway */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Gateway Timeout */
            504: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    cloud_destinations_api_cloud_get: {
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
                    "application/json": components["schemas"]["CloudDestinationsResponse"];
                };
            };
        };
    };
    add_cloud_destination_api_cloud_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CloudDestinationCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CloudDestinationsResponse"];
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
    remove_cloud_destination_api_cloud__model_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                model_id: string;
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
                    "application/json": components["schemas"]["CloudDestinationsResponse"];
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
    set_cloud_key_api_cloud__model_id__key_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                model_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CloudApiKeyUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CloudDestinationsResponse"];
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
    clear_cloud_key_api_cloud__model_id__key_delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                model_id: string;
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
                    "application/json": components["schemas"]["CloudDestinationsResponse"];
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
    set_cloud_model_api_cloud__model_id__model_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                model_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CloudModelUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CloudDestinationsResponse"];
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
    list_cloud_models_api_cloud__model_id__models_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                model_id: string;
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
                    "application/json": components["schemas"]["CloudModelOptions"];
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
    cloud_presets_api_cloud_presets_get: {
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
                    "application/json": components["schemas"]["CloudPresetsResponse"];
                };
            };
        };
    };
    list_conversations_api_conversations_get: {
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
                    "application/json": components["schemas"]["ConversationListResponse"];
                };
            };
        };
    };
    save_conversation_api_conversations_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConversationSaveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationListResponse"];
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
    delete_all_conversations_api_conversations_delete: {
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
                    "application/json": components["schemas"]["CacheClearedResponse"];
                };
            };
        };
    };
    read_conversation_api_conversations__conversation_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
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
                    "application/json": components["schemas"]["ConversationDetail"];
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
    delete_conversation_api_conversations__conversation_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
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
                    "application/json": components["schemas"]["CacheClearedResponse"];
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
    health_status_api_health_get: {
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
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
        };
    };
    metrics_status_api_metrics_get: {
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
                    "application/json": components["schemas"]["MetricsResponse"];
                };
            };
        };
    };
    get_preferences_api_preferences_get: {
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
                    "application/json": components["schemas"]["PreferencesResponse"];
                };
            };
        };
    };
    update_preferences_api_preferences_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PreferencesUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PreferencesResponse"];
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
    reset_preferences_api_preferences_reset_post: {
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
                    "application/json": components["schemas"]["PreferencesResponse"];
                };
            };
        };
    };
    telemetry_summary_api_telemetry_get: {
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
                    "application/json": components["schemas"]["TelemetrySummary"];
                };
            };
        };
    };
    clear_telemetry_api_telemetry_delete: {
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
                    "application/json": components["schemas"]["CacheClearedResponse"];
                };
            };
        };
    };
}
