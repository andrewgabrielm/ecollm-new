import { vi } from "vitest";
import type {
  CatalogSummary,
  ChatApi,
  ChatResponse,
  CloudDestinations,
  Health,
  Proposal,
  TelemetrySummary,
} from "../src/api/client";

export const requestId = "f18ec088-906b-47fe-aef1-057588e2ea2d";
export const answer: ChatResponse = {
  request_id: requestId,
  status: "success",
  answer: "A synthetic local answer.",
  route: "local",
  model_id: "mock-general",
  model_version: "synthetic-v1",
  reason: "local_primary",
  privacy_counts: [],
  restored: false,
  cache_status: "disabled",
  timings: { total_ms: 12.5, generation_ms: 10.0 },
  mock: true,
  router_type: "rules",
};
export const proposal = (): Proposal => ({
  request_id: requestId,
  status: "approval_required",
  sanitized_preview:
    "JSON for [[PII_0123456789abcdef0123456789abcdef_SYNTHETIC_STUDENT_ID_1]]\nKeep this exact whitespace.  ",
  expires_at: new Date(Date.now() + 120000).toISOString(),
  reason: "validation_failure",
  best_local_answer: "An inspected synthetic local answer.",
  proposal_id: "9c11f225-b1af-46c0-bb28-9dc473db7318",
  digest: "a".repeat(64),
  mode: "ask_before_cloud",
  model_id: "mock-cloud",
  model_version: "synthetic-v1",
  policy_version: "policy-v1",
});
export const health: Health = {
  status: "ok",
  mode: "mock",
  privacy_analyzer: "synthetic_fixture",
  eligible_local_models: 2,
  worker_limit: 1,
};
const defaultPreferences = {
  exact_cache_enabled: false,
  persistent_analytics_enabled: false,
  local_learning_enabled: false,
  workload_persistence_enabled: false,
  mode: "local_only",
};
export const noConversations = { enabled: false, conversations: [] };
export const defaultCatalog: CatalogSummary = {
  models: [
    {
      model_id: "mock-general",
      manifest_version: "v1",
      family: "mock",
      quantization: "q4",
      verified: true,
      installed: true,
      resident: false,
      eligible: true,
    },
  ],
  hardware_known: true,
  mock_mode: true,
};
export const defaultTelemetry: TelemetrySummary = {
  sample_count: 0,
  suppression_threshold: 30,
  enabled: false,
  route_counts: {},
  outcome_counts: {},
  router_type_counts: {},
  reason_counts: {},
  model_usage: [],
  latency_overall: null,
  latency_per_task: [],
  cache: { total_records: 0, cache_hits: 0, hit_rate: null },
  retention_days: 30,
};
export function fakeApi(): ChatApi {
  return {
    health: vi.fn().mockResolvedValue(health),
    chat: vi.fn().mockResolvedValue(answer),
    approval: vi.fn().mockResolvedValue({ ...answer, route: "cloud" }),
    getPreferences: vi.fn().mockResolvedValue(defaultPreferences),
    updatePreferences: vi.fn().mockResolvedValue(defaultPreferences),
    clearCache: vi.fn().mockResolvedValue(0),
    resetPreferences: vi.fn().mockResolvedValue(defaultPreferences),
    getCatalog: vi.fn().mockResolvedValue(defaultCatalog),
    getTelemetry: vi.fn().mockResolvedValue(defaultTelemetry),
    clearTelemetry: vi.fn().mockResolvedValue(0),
    getCloudDestinations: vi.fn().mockResolvedValue(defaultCloudDestinations),
    setCloudKey: vi.fn().mockResolvedValue(defaultCloudDestinations),
    setCloudModel: vi.fn().mockResolvedValue(defaultCloudDestinations),
    clearCloudKey: vi.fn().mockResolvedValue(defaultCloudDestinations),
    listCloudModels: vi.fn().mockResolvedValue({
      model_id: "groq",
      runtime_model: null,
      models: [],
      reachable: false,
    }),
    addCloudDestination: vi.fn().mockResolvedValue(defaultCloudDestinations),
    removeCloudDestination: vi.fn().mockResolvedValue(defaultCloudDestinations),
    listCloudPresets: vi.fn().mockResolvedValue([]),
    listConversations: vi.fn().mockResolvedValue(noConversations),
    saveConversation: vi.fn().mockResolvedValue(noConversations),
    readConversation: vi.fn().mockResolvedValue([]),
    deleteConversation: vi.fn().mockResolvedValue(1),
    deleteAllConversations: vi.fn().mockResolvedValue(0),
  };
}

export const defaultCloudDestinations: CloudDestinations = { destinations: [] };
