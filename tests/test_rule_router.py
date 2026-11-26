"""Pure routing acceptance checks using explicitly synthetic catalog observations."""

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.broker.context import RoutingReason
from backend.broker.router_interface import RoutingError
from backend.broker.rule_router import RuleRouter, RuleRouterConfig
from backend.contracts import TaskCategory
from backend.model_management.fixtures import build_mock_catalog
from backend.model_management.manifest import ModelCatalog, ModelManifest
from backend.model_management.registry import (
    HardwareContext,
    ModelRegistry,
    RegistrySnapshot,
    RuntimeInventory,
    RuntimeObservation,
)
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer
from backend.profiles.preferences import PreferenceProfile, RuleWeights

ALL_MODELS = ("mock-general", "mock-specialist", "mock-third")


class FixtureAnalyzer:
    version = "synthetic-routing-test-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        return ()


def payload() -> SanitizedPayload:
    return Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), "A fixed invented prompt").payload


def mock_registry(
    *,
    catalog: ModelCatalog | None = None,
    hardware: HardwareContext | None = None,
    unhealthy: tuple[str, ...] = (),
    absent: tuple[str, ...] = (),
) -> ModelRegistry:
    chosen = catalog or build_mock_catalog()
    registry = ModelRegistry(chosen, mock_mode=True, hardware=hardware)
    registry.reconcile(
        RuntimeInventory(
            models=tuple(
                RuntimeObservation(
                    runtime_tag=model.runtime_tag,
                    healthy=model.id not in unhealthy,
                    resident=False,
                )
                for model in chosen.models
                if model.id not in absent
            )
        )
    )
    return registry


def changed_catalog(*, unknown: bool = False, equal: bool = False) -> ModelCatalog:
    models: list[ModelManifest] = []
    for source in build_mock_catalog().models:
        data = source.model_dump(mode="json")
        if unknown:
            data["capability_benchmark"] = []
        if equal:
            data["capability_benchmark"] = [
                {
                    "task": "qa",
                    "provenance": "synthetic",
                    "quality": 0.5,
                    "latency_ms": 500.0,
                    "benchmark_version": "a" * 64,
                }
            ]
        models.append(ModelManifest.model_validate(data))
    return ModelCatalog(models=tuple(models))


def test_ranks_two_of_three_and_binds_producing_manifest_versions() -> None:
    snapshot = ModelRegistry.mock(installed_ids=ALL_MODELS).snapshot()
    router = RuleRouter()
    plan = router.rank(payload(), TaskCategory.CODE, snapshot)
    assert plan.model_ids == ("mock-specialist", "mock-third")
    versions = {entry.manifest.id: entry.manifest.version for entry in snapshot.roster.entries}
    assert plan.manifest_versions == tuple(versions[model_id] for model_id in plan.model_ids)
    assert plan.reason == RoutingReason.RULES_RANKED
    assert plan.cloud_hint is None
    assert router.rank(payload(), TaskCategory.QA, snapshot).model_ids == (
        "mock-general",
        "mock-third",
    )
    with pytest.raises(ValidationError):
        plan.model_ids = ()


def test_defaults_start_with_two_installed_not_all_catalog_candidates() -> None:
    snapshot = ModelRegistry.mock().snapshot()
    assert set(RuleRouter().rank(payload(), TaskCategory.QA, snapshot).model_ids) == {
        "mock-general",
        "mock-specialist",
    }


def test_missing_and_unhealthy_candidates_are_not_ranked() -> None:
    snapshot = mock_registry(unhealthy=("mock-specialist",), absent=("mock-general",)).snapshot()
    plan = RuleRouter().rank(payload(), TaskCategory.CODE, snapshot)
    assert plan.model_ids == ("mock-third",)
    assert len(plan.manifest_versions) == 1
    registry = mock_registry(unhealthy=ALL_MODELS)
    empty = RuleRouter().rank(payload(), TaskCategory.CODE, registry.snapshot())
    assert empty.model_ids == ()
    assert empty.reason == RoutingReason.NO_ELIGIBLE_MODEL


def test_unavailable_runtime_has_no_ranked_candidate() -> None:
    registry = ModelRegistry.mock()
    snapshot = registry.reconcile(RuntimeInventory(available=False))
    assert RuleRouter().rank(payload(), TaskCategory.QA, snapshot).model_ids == ()


def test_unknown_observations_are_conservative_and_reported_as_fallback() -> None:
    snapshot = mock_registry(catalog=changed_catalog(unknown=True)).snapshot()
    router = RuleRouter()
    details = router.rank_details(payload(), TaskCategory.QA, snapshot)
    assert [item.model_id for item in details] == list(sorted(ALL_MODELS))
    for candidate in details:
        assert candidate.unknown_quality and candidate.unknown_latency
        assert candidate.features.capability_quality is None
        assert candidate.features.capability_latency_ms is None
        assert candidate.quality_used == 0
        assert candidate.normalized_latency == 1
        assert candidate.cold_start_penalty == 1
        assert candidate.utility == pytest.approx(-0.4)
    assert router.rank(payload(), TaskCategory.QA, snapshot).reason == RoutingReason.RULES_FALLBACK


def test_ties_break_on_model_identity_independent_of_catalog_order() -> None:
    catalog = changed_catalog(equal=True)
    first = mock_registry(catalog=catalog).snapshot()
    second = mock_registry(catalog=ModelCatalog(models=tuple(reversed(catalog.models)))).snapshot()
    router = RuleRouter()
    assert router.rank(payload(), TaskCategory.QA, first).model_ids == tuple(sorted(ALL_MODELS))[:2]
    assert (
        router.rank(payload(), TaskCategory.QA, first).model_ids
        == router.rank(payload(), TaskCategory.QA, second).model_ids
    )


def test_preferences_change_ranking_without_changing_cloud_policy_or_roster() -> None:
    snapshot = ModelRegistry.mock(installed_ids=ALL_MODELS).snapshot()
    router = RuleRouter(RuleRouterConfig(latency_reference_ms=2000))
    quality = PreferenceProfile(weights=RuleWeights(quality=1, latency=0, load=0))
    speed = PreferenceProfile(weights=RuleWeights(quality=0, latency=1, load=0))
    assert (
        router.rank(payload(), TaskCategory.CODE, snapshot, quality).model_ids[0]
        == "mock-specialist"
    )
    assert router.rank(payload(), TaskCategory.CODE, snapshot, speed).model_ids[0] == "mock-general"
    assert router.rank(payload(), TaskCategory.CODE, snapshot, speed).cloud_hint is None


def test_normalization_is_fixed_when_another_candidate_disappears() -> None:
    registry = mock_registry()
    router = RuleRouter(RuleRouterConfig(latency_reference_ms=2000))
    before = {
        row.model_id: row
        for row in router.rank_details(payload(), TaskCategory.QA, registry.snapshot())
    }
    registry.reconcile(
        RuntimeInventory(
            models=tuple(
                RuntimeObservation(runtime_tag=model.runtime_tag, healthy=True, resident=False)
                for model in build_mock_catalog().models
                if model.id != "mock-specialist"
            )
        )
    )
    after = {
        row.model_id: row
        for row in router.rank_details(payload(), TaskCategory.QA, registry.snapshot())
    }
    assert after["mock-general"].utility == before["mock-general"].utility
    assert after["mock-third"].normalized_latency == before["mock-third"].normalized_latency


def test_snapshot_hardware_is_used_and_override_mismatch_is_rejected() -> None:
    machine = HardwareContext.model_validate(
        {"platform": "linux-x86_64", "ram_bytes": 4096, "cpu_threads": 4}
    )
    snapshot = mock_registry(hardware=machine).snapshot()
    router = RuleRouter()
    for candidate in router.rank_details(payload(), TaskCategory.QA, snapshot):
        assert candidate.features.hardware_version == snapshot.hardware_version
        assert candidate.features.hardware_ram_bytes == 4096
    assert router.rank(payload(), TaskCategory.QA, snapshot, hardware=machine).model_ids
    with pytest.raises(RoutingError):
        router.rank(payload(), TaskCategory.QA, snapshot, hardware=HardwareContext())


def test_forged_eligible_snapshot_is_revalidated() -> None:
    snapshot = ModelRegistry.mock().snapshot()
    forged = snapshot.model_copy(update={"eligible_model_ids": ("not-installed",)})
    with pytest.raises(RoutingError):
        RuleRouter().rank(payload(), TaskCategory.QA, forged)
    assert isinstance(snapshot, RegistrySnapshot)


def test_invalid_configuration_preferences_and_payload_fail_without_content_echo() -> None:
    with pytest.raises(RoutingError):
        RuleRouter(RuleRouterConfig().model_copy(update={"latency_reference_ms": 0}))
    source = payload()
    invalid = SanitizedPayload.model_validate_json(source.model_dump_json())
    with pytest.raises(RoutingError) as caught:
        RuleRouter().rank(invalid, TaskCategory.QA, ModelRegistry.mock().snapshot())
    assert source.text not in str(caught.value)
    profile = PreferenceProfile().model_copy(update={"weights": {"quality": float("nan")}})
    with pytest.raises(RoutingError):
        RuleRouter().rank(source, TaskCategory.QA, ModelRegistry.mock().snapshot(), profile)


def test_router_and_features_have_no_provider_imports_or_generation_calls() -> None:
    root = Path(__file__).resolve().parents[1]
    for filename in ("rule_router.py", "feature_extractor.py", "router_interface.py"):
        tree = ast.parse((root / "backend" / "broker" / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "providers" not in (node.module or "").split(".")
            if isinstance(node, ast.Import):
                assert all("providers" not in alias.name.split(".") for alias in node.names)
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"generate", "warm_up", "health", "pull", "unload"}


def unmeasured_catalog() -> ModelCatalog:
    """The shape the real catalog ships: tasks declared, nothing benchmarked."""
    declared = {
        "mock-general": ("qa", "summarise", "reason"),
        "mock-specialist": ("code", "format", "extract"),
        "mock-third": ("classify",),
    }
    models: list[ModelManifest] = []
    for source in build_mock_catalog().models:
        data = source.model_dump(mode="json")
        data["capability_benchmark"] = [
            {"task": task, "provenance": "unknown"} for task in declared[source.id]
        ]
        models.append(ModelManifest.model_validate(data))
    return ModelCatalog(models=tuple(models))


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        (TaskCategory.CODE, "mock-specialist"),
        (TaskCategory.FORMAT, "mock-specialist"),
        (TaskCategory.QA, "mock-general"),
        (TaskCategory.SUMMARISE, "mock-general"),
        (TaskCategory.CLASSIFY, "mock-third"),
    ],
)
def test_declared_coverage_breaks_a_tie_toward_the_declaring_model(
    task: TaskCategory, expected: str
) -> None:
    # Every real catalog entry ships quality: null until benchmarking runs, so
    # all utilities are equal and ranking fell back to model id — which meant
    # the code specialist was never selected, even with an explicit code hint.
    router = RuleRouter()
    snapshot = mock_registry(catalog=unmeasured_catalog()).snapshot()
    ranked = router.rank_details(payload(), task, snapshot, PreferenceProfile())
    assert {item.unknown_quality for item in ranked} == {True}
    assert len({item.utility for item in ranked}) == 1
    assert ranked[0].model_id == expected


def test_a_measured_score_still_outranks_declared_coverage() -> None:
    # Declared coverage is catalog metadata, not a measurement, so it must never
    # override a candidate that actually scores better. The mock catalog is
    # benchmarked, so quality decides there and the tie-break never applies.
    router = RuleRouter()
    ranked = router.rank_details(
        payload(), TaskCategory.CODE, mock_registry().snapshot(), PreferenceProfile()
    )
    assert ranked[0].utility == max(item.utility for item in ranked)
    assert ranked[0].model_id == "mock-specialist"
