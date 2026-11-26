"""Step 06 component integration, with no generation, routing cascade or API server."""

import ast
import logging
from pathlib import Path
from uuid import uuid4

import pytest

from backend.broker.classifier import ClassificationError, classify_task
from backend.broker.context import RoutingReason
from backend.broker.feature_extractor import extract_features
from backend.broker.rule_router import RuleRouter
from backend.broker.validators import ResponseConstraints, validate_response
from backend.config.loader import load_catalog
from backend.contracts import TaskCategory
from backend.model_management.registry import ModelRegistry, RuntimeInventory, RuntimeObservation
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.recognisers import synthetic_spans
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer


class FixtureAnalyzer:
    version = "synthetic-rules-integration-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        return synthetic_spans(text)


def test_sanitized_task_rank_features_and_lease_share_one_immutable_view(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "STU-1234-5678"
    result = Sanitizer(FixtureAnalyzer()).sanitize(
        uuid4(), f"Write a Python function to extract invented records for {marker}"
    )
    registry = ModelRegistry.mock(installed_ids=("mock-general", "mock-specialist", "mock-third"))
    snapshot = registry.snapshot()
    with caplog.at_level(logging.DEBUG):
        classification = classify_task(result.payload)
        router = RuleRouter()
        plan = router.rank(result.payload, classification.task, snapshot)
        details = router.rank_details(result.payload, classification.task, snapshot)
    assert classification.task == TaskCategory.CODE
    assert plan.model_ids == ("mock-specialist", "mock-third")
    assert plan.cloud_hint is None
    entries = {entry.manifest.id: entry for entry in snapshot.roster.entries}
    with registry.lease(plan.model_ids, snapshot=snapshot) as lease:
        for score in details:
            row = extract_features(
                result.payload,
                classification.task,
                entries[score.model_id],
                snapshot.hardware,
                mock_mode=snapshot.mock_mode,
            )
            assert row == score.features
            assert marker not in row.model_dump_json()
        assert lease.snapshot.version == snapshot.version
        assert registry.active_leases(plan.model_ids[0]) == 1
    assert registry.active_leases(plan.model_ids[0]) == 0
    assert marker not in caplog.text
    assert marker not in plan.model_dump_json()
    result.placeholders.clear()


def test_reconciled_health_filters_new_ranking_without_mutating_inflight_snapshot() -> None:
    result = Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), "Write a Python function")
    registry = ModelRegistry.mock(installed_ids=("mock-general", "mock-specialist", "mock-third"))
    old = registry.snapshot()
    router = RuleRouter()
    first = router.rank(result.payload, TaskCategory.CODE, old)
    with registry.lease(first.model_ids, snapshot=old) as lease:
        changed = registry.reconcile(
            RuntimeInventory(
                models=tuple(
                    RuntimeObservation(
                        runtime_tag=entry.manifest.runtime_tag,
                        healthy=entry.manifest.id != "mock-specialist",
                        resident=False,
                    )
                    for entry in old.roster.entries
                )
            )
        )
        second = router.rank(result.payload, TaskCategory.CODE, changed)
        assert "mock-specialist" not in second.model_ids
        assert router.rank(result.payload, TaskCategory.CODE, lease.snapshot) == first
        assert registry.active_leases("mock-specialist") == 1
    result.placeholders.clear()


def test_real_placeholder_catalog_stays_ineligible_despite_observed_installation() -> None:
    catalog = load_catalog()
    registry = ModelRegistry(catalog)
    placeholders = [model for model in catalog.models if model.verification == "unverified"]
    verified = [model for model in catalog.models if model.verification == "verified"][:1]
    assert placeholders and verified
    snapshot = registry.reconcile(
        RuntimeInventory(
            models=tuple(
                # Verified entry observed with a different digest must also stay ineligible.
                RuntimeObservation(runtime_tag=model.runtime_tag, healthy=True, digest="0" * 64)
                for model in (*placeholders, *verified)
            )
        )
    )
    result = Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), "Explain a fixed concept")
    classification = classify_task(result.payload)
    plan = RuleRouter().rank(result.payload, classification.task, snapshot)
    assert not plan.model_ids
    assert plan.reason == RoutingReason.NO_ELIGIBLE_MODEL
    assert plan.cloud_hint is None
    result.placeholders.clear()


def test_structure_success_does_not_imply_truth_and_untrusted_input_is_rejected() -> None:
    constraints = ResponseConstraints(required_fields=("answer",))
    result = Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), "Return the answer as JSON")
    assert classify_task(result.payload, constraints=constraints).task == TaskCategory.EXTRACT
    # An explicitly wrong arithmetic claim still has the requested JSON shape.
    assert validate_response('{"answer": "2 + 2 = 5"}', constraints).valid
    assert not validate_response('{"missing_answer": true}', constraints).valid
    untrusted = SanitizedPayload.model_validate_json(result.payload.model_dump_json())
    with pytest.raises(ClassificationError):
        classify_task(untrusted)
    result.placeholders.clear()


def test_step06_components_cannot_import_providers_or_the_generation_dispatcher() -> None:
    root = Path(__file__).resolve().parents[2] / "backend"
    for relative in (
        "broker/classifier.py",
        "broker/validators.py",
        "broker/router_interface.py",
        "broker/rule_router.py",
        "broker/feature_extractor.py",
        "model_management/registry.py",
        "model_management/fixtures.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        modules = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any("providers" in module or "orchestrator" in module for module in modules)
