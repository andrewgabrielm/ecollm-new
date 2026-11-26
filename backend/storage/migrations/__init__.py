"""Trusted immutable schema source; no migration paths are accepted from users."""

import json
from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        encoded = json.dumps(
            [self.version, self.name, self.statements],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


MIGRATIONS = (
    Migration(
        version=1,
        name="storage_metadata",
        statements=(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "INSERT INTO schema_metadata (key, value) VALUES ('storage_schema', '1')",
        ),
    ),
    Migration(
        version=2,
        name="exact_cache_and_preferences",
        statements=(
            "CREATE TABLE exact_cache ("
            "cache_key TEXT PRIMARY KEY, "
            "answer TEXT NOT NULL, "
            "model_id TEXT NOT NULL, "
            "model_version TEXT NOT NULL, "
            "task TEXT NOT NULL, "
            "created_at REAL NOT NULL)",
            "CREATE INDEX idx_exact_cache_created ON exact_cache (created_at)",
            "CREATE TABLE preferences ("
            "profile_id TEXT PRIMARY KEY, "
            "data TEXT NOT NULL, "
            "updated_at REAL NOT NULL)",
        ),
    ),
    Migration(
        version=3,
        name="benchmark_observations",
        statements=(
            "CREATE TABLE observations ("
            "run_id TEXT NOT NULL, "
            "task_id TEXT NOT NULL, "
            "model_id TEXT NOT NULL, "
            "model_version TEXT NOT NULL, "
            "split TEXT NOT NULL, "
            "task_category TEXT NOT NULL, "
            "schema_version TEXT NOT NULL, "
            "feature_hash TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "error_code TEXT, "
            "quality REAL, "
            "format_valid INTEGER, "
            "input_tokens INTEGER, "
            "output_tokens INTEGER, "
            "total_ms REAL, "
            "privacy_ms REAL, "
            "routing_ms REAL, "
            "generation_ms REAL, "
            "inspection_ms REAL, "
            "load_ms REAL, "
            "prompt_ms REAL, "
            "resident INTEGER, "
            "policy_version TEXT NOT NULL, "
            "mock INTEGER NOT NULL, "
            "created_at REAL NOT NULL, "
            "PRIMARY KEY (run_id, task_id, model_id, model_version))",
            "CREATE INDEX idx_observations_run ON observations (run_id)",
            "CREATE INDEX idx_observations_task_model "
            "ON observations (task_id, model_id, model_version)",
        ),
    ),
    Migration(
        version=4,
        name="observation_features",
        statements=("ALTER TABLE observations ADD COLUMN features_json TEXT",),
    ),
    Migration(
        version=5,
        name="telemetry_records",
        statements=(
            "CREATE TABLE telemetry ("
            "schema_version TEXT NOT NULL, "
            "request_id TEXT NOT NULL, "
            "created_at REAL NOT NULL, "
            "task TEXT NOT NULL, "
            "route TEXT NOT NULL, "
            "reason TEXT NOT NULL, "
            "model_id TEXT, "
            "model_version TEXT, "
            "router_type TEXT NOT NULL, "
            "router_version TEXT NOT NULL, "
            "outcome TEXT NOT NULL, "
            "error_code TEXT, "
            "cache_status TEXT NOT NULL, "
            "total_ms REAL, "
            "privacy_ms REAL, "
            "routing_ms REAL, "
            "generation_ms REAL, "
            "input_tokens INTEGER, "
            "output_tokens INTEGER, "
            "pii_type_counts_json TEXT NOT NULL, "
            "mock INTEGER NOT NULL, "
            "policy_version TEXT NOT NULL, "
            "PRIMARY KEY (request_id))",
            "CREATE INDEX idx_telemetry_created ON telemetry (created_at)",
        ),
    ),
    Migration(
        version=6,
        name="saved_conversations",
        statements=(
            # Opt-in chat history. This is the one place raw prompts and answers
            # are written to disk, so it is gated on workload_persistence_enabled
            # and nothing else reads these tables. Turning the preference off
            # deletes every row.
            "CREATE TABLE conversations ("
            "id TEXT NOT NULL, "
            "title TEXT NOT NULL, "
            "created_at REAL NOT NULL, "
            "updated_at REAL NOT NULL, "
            "PRIMARY KEY (id))",
            "CREATE INDEX idx_conversations_updated ON conversations (updated_at)",
            "CREATE TABLE conversation_messages ("
            "conversation_id TEXT NOT NULL, "
            "position INTEGER NOT NULL, "
            "role TEXT NOT NULL CHECK (role IN ('user', 'assistant')), "
            "content TEXT NOT NULL, "
            "model_id TEXT, "
            "created_at REAL NOT NULL, "
            "PRIMARY KEY (conversation_id, position), "
            "FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE)",
        ),
    ),
    Migration(
        version=7,
        name="owner_cloud_destinations",
        statements=(
            # Owner-added cloud destinations. Endpoints are validated against the
            # same rules as the packaged ones before a row is written; API keys
            # are never stored here and stay in process memory.
            "CREATE TABLE owner_cloud_destinations ("
            "model_id TEXT NOT NULL, "
            "display_name TEXT NOT NULL, "
            "endpoint TEXT NOT NULL, "
            "health_endpoint TEXT, "
            "created_at REAL NOT NULL, "
            "PRIMARY KEY (model_id))",
        ),
    ),
)
