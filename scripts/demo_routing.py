"""Offline Step 06 demonstration: invented fixtures, no provider or generation."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from backend.broker.classifier import classify_task
from backend.broker.feature_extractor import FEATURE_SCHEMA_HASH, FEATURE_SCHEMA_VERSION
from backend.broker.rule_router import RuleRouter
from backend.broker.validators import ResponseConstraints, validate_response
from backend.contracts import TaskCategory
from backend.model_management.registry import ModelRegistry
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.recognisers import synthetic_spans
from backend.privacy.sanitizer import Sanitizer


class DemoAnalyzer:
    version = "synthetic-routing-demo-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        return synthetic_spans(text)


def demonstrate(*, all_installed: bool, task_hint: TaskCategory | None) -> dict[str, object]:
    chosen = ("mock-general", "mock-specialist", "mock-third") if all_installed else None
    registry = ModelRegistry.mock(installed_ids=chosen)
    snapshot = registry.snapshot()
    result = Sanitizer(DemoAnalyzer()).sanitize(
        uuid4(), "Write a Python function to extract invented records for STU-1234-5678"
    )
    try:
        classification = classify_task(result.payload, task_hint=task_hint)
        router = RuleRouter()
        plan = router.rank(result.payload, classification.task, snapshot)
        with registry.lease(plan.model_ids, snapshot=snapshot) as lease:
            active_leases = registry.active_leases(plan.model_ids[0])
            assert lease.snapshot.version == snapshot.version
        shape = validate_response(
            '{"answer": "2 + 2 = 5"}', ResponseConstraints(required_fields=("answer",))
        )
        return {
            "demonstration": "OFFLINE SYNTHETIC FIXTURES ONLY",
            "catalogued_models": len(snapshot.catalog_statuses),
            "installed_mock_models": len(snapshot.roster.entries),
            "task": classification.task.value,
            "classification_reason": classification.reason.value,
            "ranked_models": list(plan.model_ids),
            "routing_reason": plan.reason.value,
            "active_lease_during_use": active_leases,
            "active_lease_after_release": registry.active_leases(plan.model_ids[0]),
            "structure_validation": shape.reason.value,
            "factual_correctness_evaluated": False,
            "feature_schema": FEATURE_SCHEMA_VERSION,
            "feature_schema_hash": FEATURE_SCHEMA_HASH,
            "generation_performed": False,
        }
    finally:
        result.placeholders.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all-installed",
        action="store_true",
        help="Simulate all three fixture candidates installed",
    )
    parser.add_argument(
        "--task",
        choices=[task.value for task in TaskCategory],
        help="Explicit task hint overrides classification",
    )
    args = parser.parse_args()
    summary = demonstrate(
        all_installed=args.all_installed,
        task_hint=TaskCategory(args.task) if args.task is not None else None,
    )
    directory = (
        Path(__file__).resolve().parents[1]
        / "evaluation"
        / "results"
        / f"routing-demo-{uuid4().hex[:12]}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    artifact = directory / "summary.json"
    artifact.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("OFFLINE SYNTHETIC FIXTURES ONLY — no network, provider or real scores")
    for key, value in summary.items():
        if key != "demonstration":
            print(f"{key}: {value}")
    print(f"Artifact: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
