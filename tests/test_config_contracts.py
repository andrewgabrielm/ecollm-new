"""Step 02 catalog, consent, configuration and version acceptance tests."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.config.loader import (
    AppConfig,
    ComputeBudgets,
    ConfigError,
    PolicyConfig,
    load_catalog,
    load_policy,
    load_settings,
    load_yaml,
)
from backend.contracts import ExecutionMode, TaskCategory
from backend.model_management.initial_install import trusted_manifest
from backend.model_management.manifest import (
    BenchmarkProvenance,
    CapabilityBenchmark,
    LicenseReview,
    LicenseStatus,
    ModelCatalog,
    ModelManifest,
    RosterEntry,
    RosterSnapshot,
    SwapProposal,
    VerificationStatus,
)
from backend.profiles.preferences import PreferenceProfile, RuleWeights
from backend.versions import ARTIFACT_VERSION, POLICY_VERSION, SCHEMA_VERSION, canonical_hash


def verified_manifest() -> ModelManifest:
    """Invented structurally verified identity, never a claim about installed models."""
    data = load_catalog().models[0].model_dump(mode="json")
    data.update(
        pinned_digest="a" * 64,
        source={"uri": "https://example.invalid/synthetic", "revision": "b" * 40},
        license_review={
            "status": "approved",
            "identifier": "SYNTHETIC-TEST-LICENSE",
            "reference": {"uri": "https://example.invalid/license"},
        },
        quantization="synthetic-q4",
        size_bytes=100,
        memory_bytes=200,
        platforms=["linux-x86_64"],
        verification="verified",
    )
    return ModelManifest.model_validate(data)


def test_all_persistence_consent_defaults_are_off_and_independent() -> None:
    profile = PreferenceProfile()
    consents = (
        "exact_cache_enabled",
        "semantic_cache_enabled",
        "workload_persistence_enabled",
        "local_learning_enabled",
        "persistent_analytics_enabled",
    )
    assert profile.mode == ExecutionMode.LOCAL_ONLY
    assert all(getattr(profile, name) is False for name in consents)
    for selected in consents:
        configured = PreferenceProfile.model_validate({selected: True})
        assert all(getattr(configured, name) is (name == selected) for name in consents)
        with pytest.raises(ValidationError):
            PreferenceProfile.model_validate({selected: "true"})
    with pytest.raises(ValidationError):
        PreferenceProfile.model_validate({"prompt_history": True})


def test_default_yaml_is_valid_and_catalog_is_ineligible_in_real_mode() -> None:
    settings = load_settings({})
    policy = load_policy()
    catalog = load_catalog()
    assert settings.initial_mode == ExecutionMode.LOCAL_ONLY
    assert settings.preferences == PreferenceProfile()
    assert settings.budgets == ComputeBudgets()
    assert settings.rule_weights == RuleWeights()
    assert policy.cloud_preferred_categories == ()
    assert not policy.allow_no_local_model_cloud
    placeholders = tuple(model for model in catalog.models if model.id.startswith("placeholder-"))
    verified = tuple(model for model in catalog.models if not model.id.startswith("placeholder-"))
    assert len(placeholders) == 2
    assert all(model.pinned_digest is None for model in placeholders)
    assert all(model.verification == VerificationStatus.UNVERIFIED for model in placeholders)
    # Only owner-reviewed official-library identities (Step 09) are eligible in real mode.
    assert catalog.eligible_manifests(mock_mode=False) == verified
    assert {model.id for model in verified} == {"qwen25-1-5b", "qwen25-coder-1-5b", "qwen25-0-5b"}
    for model in verified:
        assert trusted_manifest(catalog, model.id) == model
        assert model.license_review.status == "approved"
        assert not any(item.provenance != "unknown" for item in model.capability_benchmark)
    with pytest.raises(ValidationError):
        settings.preferences.exact_cache_enabled = True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "../bad"),
        ("runtime_tag", "https://example.invalid/model"),
        ("runtime_tag", "a/../b:latest"),
        ("runtime_tag", "a//b:latest"),
        ("runtime_tag", "a/:latest"),
        ("pinned_digest", "not-pinned"),
        ("source", {"uri": "http://example.invalid/model"}),
        ("source", {"uri": "https://user:pass@example.invalid/model"}),
        ("source", {"uri": "https://example.invalid/model?key=private"}),
        ("source", {"uri": "https://bad host.invalid/model"}),
        ("source", {"uri": "https://example.invalid/mo\ndel"}),
        ("source", {"uri": "https://example.invalid/\\model"}),
        ("source", {"uri": "https://example.invalid/model", "revision": "main"}),
        ("size_bytes", -1),
        ("size_bytes", True),
        ("memory_bytes", "1000"),
        ("platforms", ["unmeasured-architecture"]),
        ("verification", "verified"),
        ("unexpected", "field"),
    ],
)
def test_manifest_rejects_malformed_or_unsubstantiated_fields(field: str, value: object) -> None:
    data = load_catalog().models[0].model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_verified_identity_and_catalog_versions() -> None:
    model = verified_manifest()
    catalog = ModelCatalog(
        models=(load_catalog().models[0], model.model_copy(update={"id": "other"}))
    )
    assert catalog.eligible_manifests(mock_mode=False) == (catalog.models[1],)
    assert ModelManifest.model_validate_json(model.model_dump_json()).version == model.version
    changed = ModelManifest.model_validate({**model.model_dump(), "pinned_digest": "c" * 64})
    assert changed.version != model.version
    with pytest.raises(ValidationError):
        ModelCatalog(models=(model, model))
    with pytest.raises(ValidationError):
        LicenseReview(status=LicenseStatus.APPROVED)
    with pytest.raises(ValidationError):
        CapabilityBenchmark(task=TaskCategory.QA, quality=0.9)
    with pytest.raises(ValidationError):
        CapabilityBenchmark(
            task=TaskCategory.QA, provenance=BenchmarkProvenance.MEASURED, quality=0.9
        )
    with pytest.raises(ValidationError):
        CapabilityBenchmark(task=TaskCategory.QA, quality=float("nan"))


def test_roster_is_immutable_bounded_and_separates_installed_from_resident() -> None:
    entry = RosterEntry(manifest=verified_manifest(), installed=True)
    snapshot = RosterSnapshot(entries=(entry,))
    assert snapshot.entries[0].resident is False
    assert snapshot.entries[0].healthy is False
    assert (
        RosterSnapshot.model_validate_json(snapshot.model_dump_json()).version == snapshot.version
    )
    with pytest.raises(ValidationError):
        snapshot.entries = ()
    with pytest.raises(ValidationError):
        RosterSnapshot(entries=(entry, entry))
    with pytest.raises(ValidationError):
        RosterSnapshot(entries=(entry,) * 4)
    with pytest.raises(ValidationError):
        RosterEntry(manifest=entry.manifest, resident=True)


def test_swap_contract_binds_identity_roster_policy_and_deletion() -> None:
    proposal = SwapProposal(
        proposal_id=uuid4(),
        install_manifest=verified_manifest(),
        expected_roster_version="c" * 64,
        retire_model_ids=("retiring",),
        disk_headroom_bytes=500,
        required_staging_bytes=300,
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    assert proposal.delete_retired is False
    assert proposal.policy_version == POLICY_VERSION
    assert SwapProposal.model_validate_json(proposal.model_dump_json()).version == proposal.version
    changes: list[dict[str, object]] = [
        {"install_manifest": load_catalog().models[0]},
        {"expected_roster_version": "not-a-version"},
        {"policy_version": "unsupported"},
        {"disk_headroom_bytes": 1},
        {"retire_model_ids": (proposal.install_manifest.id,)},
        {"retire_model_ids": (), "delete_retired": True},
        {"expires_at": datetime(2026, 1, 1)},
    ]
    for change in changes:
        with pytest.raises(ValidationError):
            SwapProposal.model_validate({**proposal.model_dump(), **change})


def test_canonical_versions_are_deterministic_and_domain_separated() -> None:
    left = {"b": [1, "a  b"], "a": {"x": False}}
    right = {"a": {"x": False}, "b": [1, "a  b"]}
    assert canonical_hash(left) == canonical_hash(right)
    assert canonical_hash(left) == canonical_hash(json.loads(json.dumps(right)))
    assert canonical_hash([1, 2]) != canonical_hash([2, 1])
    assert canonical_hash("a b") != canonical_hash("a  b")
    assert canonical_hash(True) != canonical_hash(1)
    versions = {
        canonical_hash(left, namespace=v)
        for v in (SCHEMA_VERSION, POLICY_VERSION, ARTIFACT_VERSION)
    }
    assert len(versions) == 3
    assert load_settings({}).version == load_settings({"CLOUD_API_KEY": "invented"}).version
    assert load_policy().version == PolicyConfig().version
    assert PolicyConfig(hard_block_entity_types=()).version != load_policy().version


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "key"}, object(), {"a": {1}}])
def test_hashes_reject_noncanonical_values(value: object) -> None:
    with pytest.raises(ValueError):
        canonical_hash(value)


@pytest.mark.parametrize(
    "yaml_text",
    [
        "mock_mode: true\nmock_mode: false\n",
        "mock_mode: yes\ncloud_api_key: invented-private-value\n",
        "budgets: &shared {}\npreferences: *shared\n",
        "!!python/object:unsafe {}",
        "mock_mode: [\n",
        "mock_mode: 'true'\n",
        "managed_model_limit: 4\n",
        "schema_version: unsupported\n",
        "? [a, b]\n: value\n",
        "#" * 65_537,
    ],
)
def test_yaml_rejection_has_safe_errors(tmp_path: Path, yaml_text: str) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_yaml(path, AppConfig)
    assert "invented-private-value" not in str(caught.value)
    assert not caught.value.__cause__


def test_missing_yaml_and_env_overrides_are_safe(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_yaml(tmp_path / "missing.yaml", AppConfig)
    custom = tmp_path / "settings.yaml"
    custom.write_text("mock_mode: false\n", encoding="utf-8")
    assert load_settings({}, config_path=custom).mock_mode is False
    assert load_settings({"MOCK_MODE": "1"}, config_path=custom).mock_mode is True
    marker = "invented-private-value"
    settings = load_settings({"CLOUD_API_KEY": marker})
    assert marker not in repr(settings)
    assert marker not in settings.model_dump_json()
    assert settings.cloud_api_key is not None
    assert settings.cloud_api_key.get_secret_value() == marker


@pytest.mark.parametrize(
    "values",
    [
        {"local_connection_retries": 2},
        {"cloud_retries": 1},
        {"local_seconds": 0},
        {"overall_seconds": 10},
        {"resume_seconds": 121},
        {"health_ttl_seconds": float("nan")},
    ],
)
def test_budget_bounds(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ComputeBudgets.model_validate(values)


@pytest.mark.parametrize("value", [True, False, "1.0"])
def test_numeric_contracts_reject_coerced_boolean_or_string(value: object) -> None:
    for contract, field in ((ComputeBudgets, "local_seconds"), (RuleWeights, "quality")):
        with pytest.raises(ValidationError):
            contract.model_validate({field: value})
    with pytest.raises(ValidationError):
        CapabilityBenchmark.model_validate(
            {
                "task": "qa",
                "quality": value,
                "provenance": "measured",
                "benchmark_version": "d" * 64,
            }
        )
