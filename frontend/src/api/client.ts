import { z } from "zod";
import type { components } from "./generated";

export type ChatRequest = components["schemas"]["ChatRequest"];
export type ApprovalRequest = components["schemas"]["ApprovalRequest"];
export type ChatResponse = components["schemas"]["ChatResponse"] & {
  status: "success";
};
export type Proposal = components["schemas"]["ApprovalRequiredResponse"] & {
  status: "approval_required";
  proposal_id: string;
  digest: string;
  model_id: string;
  model_version: string;
  policy_version: string;
};
export type Health = components["schemas"]["HealthResponse"];
export type Outcome =
  | ChatResponse
  | Proposal
  | (components["schemas"]["CancelledResponse"] & { status: "cancelled" });
type ErrorCode = components["schemas"]["ErrorCode"];
const id = z.string().uuid();
const identifier = z.string().regex(/^[a-z0-9][a-z0-9._-]{0,63}$/);
const version = z.string().min(1).max(128);
const ms = z.number().finite().nonnegative();
const nullable = <T extends z.ZodTypeAny>(schema: T) =>
  schema.nullable().optional();
const success = z
  .object({
    request_id: id,
    status: z.literal("success"),
    answer: z.string().min(1).max(64000),
    route: z.enum(["local", "cloud"]),
    model_id: identifier,
    model_version: version,
    reason: identifier,
    privacy_counts: z.array(
      z
        .object({
          entity_type: z.string().regex(/^[A-Z][A-Z_]{0,63}$/),
          count: z.number().int().positive(),
        })
        .strict(),
    ),
    restored: z.boolean(),
    cache_status: z.enum([
      "disabled",
      "bypass",
      "miss",
      "exact_hit",
      "semantic_hit",
    ]),
    cache_provenance: nullable(
      z
        .object({
          model_id: identifier,
          model_version: version,
          policy_version: version,
          schema_version: version,
          source: z.literal("local"),
        })
        .strict(),
    ),
    timings: z
      .object({
        total_ms: ms,
        privacy_ms: nullable(ms),
        routing_ms: nullable(ms),
        generation_ms: nullable(ms),
      })
      .strict(),
    estimated_cost_usd: nullable(ms),
    manifest_version: nullable(version),
    task: nullable(
      z.enum([
        "qa",
        "summarise",
        "extract",
        "classify",
        "reason",
        "format",
        "code",
      ]),
    ),
    router_type: z.union([z.literal("rules"), z.literal("lightgbm")]),
    router_version: nullable(version),
    mock: z.boolean(),
    generation_metrics: nullable(
      z
        .object({
          input_tokens: nullable(z.number().int().nonnegative()),
          output_tokens: nullable(z.number().int().nonnegative()),
          runtime_total_ms: nullable(ms),
          load_ms: nullable(ms),
          prompt_ms: nullable(ms),
          generation_ms: nullable(ms),
          output_tokens_per_second: nullable(ms),
        })
        .strict(),
    ),
  })
  .strict();
const proposal = z
  .object({
    request_id: id,
    status: z.literal("approval_required"),
    sanitized_preview: z.string().min(1).max(64000),
    expires_at: z.string().datetime({ offset: true }),
    reason: z.enum([
      "local_error",
      "validation_failure",
      "no_local_model",
      "cloud_preferred",
      "cloud_only",
    ]),
    best_local_answer: nullable(z.string().max(64000)),
    proposal_id: id,
    digest: z.string().regex(/^[a-f0-9]{64}$/),
    mode: z.literal("ask_before_cloud"),
    model_id: identifier,
    model_version: version,
    policy_version: version,
  })
  .strict();
const cancelled = z
  .object({
    request_id: id,
    status: z.literal("cancelled"),
    reason: z.enum(["decline", "cancel"]),
  })
  .strict();
const failure = z
  .object({
    request_id: id,
    status: z.literal("error"),
    error_code: z.string(),
    retryable: z.boolean(),
    answer: nullable(
      z.literal(
        "[Response withheld: privacy inspection could not confirm safe output.]",
      ),
    ),
  })
  .strict();
const health = z
  .object({
    status: z.enum(["ok", "degraded"]),
    mode: z.enum(["mock", "injected", "real"]),
    privacy_analyzer: z.string(),
    eligible_local_models: z.number().int().min(0).max(3),
    worker_limit: z.literal(1),
    notice: nullable(z.literal("ollama_unavailable_using_mock")),
  })
  .strict();

const messages: Partial<Record<ErrorCode, string>> = {
  invalid_request: "The request was rejected. Check the input and try again.",
  unavailable: "No eligible model is available for this request.",
  timeout: "The request reached its time limit. Try a shorter request.",
  policy_blocked: "Privacy policy blocked this action.",
  approval_conflict:
    "This approval expired or was already used. Send a new request.",
  validation_failed: "The models did not return the required structure.",
  privacy_failed: "Privacy scanning failed. Generation was blocked.",
  output_blocked:
    "The response was withheld because privacy inspection could not confirm safe output.",
  internal: "The broker could not complete this request.",
};
export class ApiError extends Error {
  constructor(public readonly code: string) {
    super(
      messages[code as ErrorCode] ??
        (code === "network"
          ? "Cannot reach the local broker. Start the backend and reconnect."
          : code === "cancelled"
            ? "The request was cancelled."
            : "The broker returned an invalid response."),
    );
    this.name = "ApiError";
  }
}
export type Preferences = z.infer<typeof preferences>;
const preferences = z
  .object({
    exact_cache_enabled: z.boolean(),
    persistent_analytics_enabled: z.boolean(),
    local_learning_enabled: z.boolean(),
    workload_persistence_enabled: z.boolean(),
    mode: z.string(),
  })
  .strict();
const catalogModel = z
  .object({
    model_id: identifier,
    manifest_version: version,
    family: z.string(),
    quantization: z.string(),
    verified: z.boolean(),
    installed: z.boolean(),
    resident: z.boolean().nullable().optional(),
    eligible: z.boolean(),
  })
  .strict();
const catalog = z
  .object({
    models: z.array(catalogModel),
    hardware_known: z.boolean(),
    mock_mode: z.boolean(),
  })
  .strict();
export type CatalogSummary = z.infer<typeof catalog>;

const conversationSummary = z
  .object({
    id: id,
    title: z.string().min(1).max(80),
    created_at: z.number(),
    updated_at: z.number(),
    message_count: z.number().int().nonnegative(),
  })
  .strict();
const conversationList = z
  .object({
    enabled: z.boolean(),
    conversations: z.array(conversationSummary),
  })
  .strict();
const conversationMessage = z
  .object({
    role: z.enum(["user", "assistant"]),
    content: z.string().min(1).max(64000),
    model_id: nullable(identifier),
  })
  .strict();
const conversationDetail = z
  .object({ id: id, messages: z.array(conversationMessage) })
  .strict();
export type ConversationSummary = z.infer<typeof conversationSummary>;
export type ConversationList = z.infer<typeof conversationList>;
export type ConversationMessage = z.infer<typeof conversationMessage>;

const cloudDestination = z
  .object({
    model_id: identifier,
    model_version: version,
    display_name: z.string().min(1).max(64),
    endpoint: z.string().url(),
    runtime_model: nullable(z.string().min(1).max(128)),
    key_available: z.boolean(),
    key_source: z.enum(["env", "runtime", "none"]),
  })
  .strict();
const cloudDestinations = z
  .object({
    destinations: z.array(cloudDestination),
  })
  .strict();
const cloudModelOptions = z
  .object({
    model_id: identifier,
    runtime_model: nullable(z.string().min(1).max(128)),
    models: z.array(z.string().min(1).max(128)),
    reachable: z.boolean(),
  })
  .strict();
export type CloudModelOptions = z.infer<typeof cloudModelOptions>;
const cloudPreset = z
  .object({
    model_id: identifier,
    display_name: z.string().min(1).max(64),
    endpoint: z.string().url(),
    health_endpoint: z.string().url(),
    note: z.string(),
  })
  .strict();
const cloudPresets = z.object({ presets: z.array(cloudPreset) }).strict();
export type CloudPreset = z.infer<typeof cloudPreset>;
export type CloudDestinationInfo = z.infer<typeof cloudDestination>;
export type CloudDestinations = z.infer<typeof cloudDestinations>;
const cacheClear = z
  .object({
    deleted: z.number().int().nonnegative(),
  })
  .strict();

const perTaskLatency = z
  .object({
    task: z.string(),
    sample_count: z.number().int().nonnegative(),
    median_ms: nullable(ms),
    p90_ms: nullable(ms),
  })
  .strict();
const telemetrySummary = z
  .object({
    schema_version: z.literal("telemetry-summary-v1").optional(),
    sample_count: z.number().int().nonnegative(),
    suppression_threshold: z.number().int().positive(),
    enabled: z.boolean(),
    route_counts: z.record(z.string(), z.number().int().nonnegative()),
    outcome_counts: z.record(z.string(), z.number().int().nonnegative()),
    router_type_counts: z.record(z.string(), z.number().int().nonnegative()),
    reason_counts: z.record(z.string(), z.number().int().nonnegative()),
    model_usage: z.array(
      z
        .object({
          model_id: identifier,
          model_version: version,
          count: z.number().int().nonnegative(),
        })
        .strict(),
    ),
    latency_overall: perTaskLatency.nullable(),
    latency_per_task: z.array(perTaskLatency),
    cache: z
      .object({
        total_records: z.number().int().nonnegative(),
        cache_hits: z.number().int().nonnegative(),
        hit_rate: nullable(ms),
      })
      .strict(),
    retention_days: z.number().int().positive(),
  })
  .strict();
export type TelemetrySummary = z.infer<typeof telemetrySummary>;

export interface ChatApi {
  health(signal?: AbortSignal): Promise<Health>;
  chat(request: ChatRequest, signal?: AbortSignal): Promise<Outcome>;
  approval(
    requestId: string,
    request: ApprovalRequest,
    signal?: AbortSignal,
  ): Promise<Outcome>;
  getPreferences(signal?: AbortSignal): Promise<Preferences>;
  updatePreferences(
    update: {
      exact_cache_enabled?: boolean;
      persistent_analytics_enabled?: boolean;
      local_learning_enabled?: boolean;
    },
    signal?: AbortSignal,
  ): Promise<Preferences>;
  resetPreferences(signal?: AbortSignal): Promise<Preferences>;
  clearCache(signal?: AbortSignal): Promise<number>;
  getCatalog(signal?: AbortSignal): Promise<CatalogSummary>;
  getTelemetry(signal?: AbortSignal): Promise<TelemetrySummary>;
  clearTelemetry(signal?: AbortSignal): Promise<number>;
  getCloudDestinations(signal?: AbortSignal): Promise<CloudDestinations>;
  setCloudKey(
    modelId: string,
    apiKey: string,
    signal?: AbortSignal,
  ): Promise<CloudDestinations>;
  setCloudModel(
    modelId: string,
    runtimeModel: string,
    signal?: AbortSignal,
  ): Promise<CloudDestinations>;
  clearCloudKey(
    modelId: string,
    signal?: AbortSignal,
  ): Promise<CloudDestinations>;
  listCloudModels(
    modelId: string,
    signal?: AbortSignal,
  ): Promise<CloudModelOptions>;
  addCloudDestination(
    destination: {
      model_id: string;
      display_name: string;
      endpoint: string;
      health_endpoint: string | null;
    },
    signal?: AbortSignal,
  ): Promise<CloudDestinations>;
  removeCloudDestination(
    modelId: string,
    signal?: AbortSignal,
  ): Promise<CloudDestinations>;
  listCloudPresets(signal?: AbortSignal): Promise<CloudPreset[]>;
  listConversations(signal?: AbortSignal): Promise<ConversationList>;
  saveConversation(
    conversationId: string,
    messages: ConversationMessage[],
    signal?: AbortSignal,
  ): Promise<ConversationList>;
  readConversation(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<ConversationMessage[]>;
  deleteConversation(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<number>;
  deleteAllConversations(signal?: AbortSignal): Promise<number>;
}

export function createClient(
  transport: typeof fetch = (...args) => fetch(...args),
): ChatApi {
  async function request<T>(
    path: string,
    method: string,
    body: object | undefined,
    parse: (value: unknown, status: number) => T,
    signal?: AbortSignal,
  ): Promise<T> {
    const deadline = AbortSignal.timeout(
      path === "/api/health"
        ? 5000
        : path.endsWith("/approval")
          ? 45000
          : 135000,
    );
    const combined = signal ? AbortSignal.any([signal, deadline]) : deadline;
    try {
      const response = await transport(path, {
        method,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
        ...(body
          ? {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            }
          : {}),
        signal: combined,
      });
      if (!response.headers.get("content-type")?.startsWith("application/json"))
        throw new ApiError("invalid_response");
      const value: unknown = await response.json();
      if (!response.ok) {
        const parsed = failure.safeParse(value);
        throw new ApiError(
          parsed.success ? parsed.data.error_code : "invalid_response",
        );
      }
      return parse(value, response.status);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (signal?.aborted) throw new ApiError("cancelled");
      if (deadline.aborted) throw new ApiError("timeout");
      if (error instanceof z.ZodError || error instanceof SyntaxError)
        throw new ApiError("invalid_response");
      throw new ApiError("network");
    }
  }
  const parseOutcome = (value: unknown, status: number): Outcome => {
    const parsed = z
      .discriminatedUnion("status", [success, proposal, cancelled])
      .parse(value);
    if (status !== (parsed.status === "approval_required" ? 202 : 200))
      throw new ApiError("invalid_response");
    return parsed as Outcome;
  };
  const parseHealth = (value: unknown, status: number): Health => {
    if (status !== 200) throw new ApiError("invalid_response");
    return health.parse(value) as Health;
  };
  const parseOk =
    <T>(schema: z.ZodType<T>) =>
    (value: unknown, status: number): T => {
      if (status !== 200) throw new ApiError("invalid_response");
      return schema.parse(value);
    };
  return {
    health: async (signal) =>
      request("/api/health", "GET", undefined, parseHealth, signal),
    chat: async (body, signal) =>
      request("/api/chat", "POST", body, parseOutcome, signal),
    approval: async (requestId, body, signal) => {
      if (!id.safeParse(requestId).success)
        throw new ApiError("invalid_request");
      return request(
        `/api/chat/${encodeURIComponent(requestId)}/approval`,
        "POST",
        body,
        parseOutcome,
        signal,
      );
    },
    getPreferences: async (signal) =>
      request(
        "/api/preferences",
        "GET",
        undefined,
        parseOk(preferences),
        signal,
      ),
    updatePreferences: async (update, signal) =>
      request("/api/preferences", "PUT", update, parseOk(preferences), signal),
    resetPreferences: async (signal) =>
      request(
        "/api/preferences/reset",
        "POST",
        undefined,
        parseOk(preferences),
        signal,
      ),
    getCatalog: async (signal) =>
      request("/api/catalog", "GET", undefined, parseOk(catalog), signal),
    clearCache: async (signal) => {
      const result = await request(
        "/api/cache",
        "DELETE",
        undefined,
        parseOk(cacheClear),
        signal,
      );
      return result.deleted;
    },
    getTelemetry: async (signal) =>
      request(
        "/api/telemetry",
        "GET",
        undefined,
        parseOk(telemetrySummary),
        signal,
      ),
    clearTelemetry: async (signal) => {
      const result = await request(
        "/api/telemetry",
        "DELETE",
        undefined,
        parseOk(cacheClear),
        signal,
      );
      return result.deleted;
    },
    getCloudDestinations: async (signal) =>
      request(
        "/api/cloud",
        "GET",
        undefined,
        parseOk(cloudDestinations),
        signal,
      ),
    setCloudKey: async (modelId, apiKey, signal) =>
      request(
        `/api/cloud/${encodeURIComponent(modelId)}/key`,
        "PUT",
        { api_key: apiKey },
        parseOk(cloudDestinations),
        signal,
      ),
    setCloudModel: async (modelId, runtimeModel, signal) =>
      request(
        `/api/cloud/${encodeURIComponent(modelId)}/model`,
        "PUT",
        { runtime_model: runtimeModel },
        parseOk(cloudDestinations),
        signal,
      ),
    listCloudModels: async (modelId, signal) =>
      request(
        `/api/cloud/${encodeURIComponent(modelId)}/models`,
        "GET",
        undefined,
        parseOk(cloudModelOptions),
        signal,
      ),
    addCloudDestination: async (destination, signal) =>
      request(
        "/api/cloud",
        "POST",
        destination,
        parseOk(cloudDestinations),
        signal,
      ),
    removeCloudDestination: async (modelId, signal) =>
      request(
        `/api/cloud/${encodeURIComponent(modelId)}`,
        "DELETE",
        undefined,
        parseOk(cloudDestinations),
        signal,
      ),
    listCloudPresets: async (signal) =>
      (
        await request(
          "/api/cloud/presets",
          "GET",
          undefined,
          parseOk(cloudPresets),
          signal,
        )
      ).presets,
    listConversations: async (signal) =>
      request(
        "/api/conversations",
        "GET",
        undefined,
        parseOk(conversationList),
        signal,
      ),
    saveConversation: async (conversationId, messages, signal) =>
      request(
        "/api/conversations",
        "PUT",
        { id: conversationId, messages },
        parseOk(conversationList),
        signal,
      ),
    readConversation: async (conversationId, signal) =>
      (
        await request(
          `/api/conversations/${encodeURIComponent(conversationId)}`,
          "GET",
          undefined,
          parseOk(conversationDetail),
          signal,
        )
      ).messages,
    deleteConversation: async (conversationId, signal) =>
      (
        await request(
          `/api/conversations/${encodeURIComponent(conversationId)}`,
          "DELETE",
          undefined,
          parseOk(cacheClear),
          signal,
        )
      ).deleted,
    deleteAllConversations: async (signal) =>
      (
        await request(
          "/api/conversations",
          "DELETE",
          undefined,
          parseOk(cacheClear),
          signal,
        )
      ).deleted,
    clearCloudKey: async (modelId, signal) =>
      request(
        `/api/cloud/${encodeURIComponent(modelId)}/key`,
        "DELETE",
        undefined,
        parseOk(cloudDestinations),
        signal,
      ),
  };
}
