"""Invented rule-routing examples only; no measured model capability claims."""

from backend.contracts import TaskCategory
from backend.model_management.manifest import (
    BenchmarkProvenance,
    CapabilityBenchmark,
    ManifestSource,
    ModelCatalog,
    ModelManifest,
)
from backend.versions import canonical_hash

FIXTURE_VERSION = canonical_hash("invented-routing-fixtures-v1", namespace="synthetic-benchmark-v1")


def build_mock_catalog() -> ModelCatalog:
    """Three catalog candidates, with two installed by ModelRegistry.mock by default."""
    models = []
    for name, latency in (
        ("mock-general", 1000.0),
        ("mock-specialist", 1500.0),
        ("mock-third", 2000.0),
    ):
        capabilities = []
        for task in TaskCategory:
            quality = 0.5
            if name == "mock-general":
                quality = 0.7 if task != TaskCategory.CODE else 0.3
            elif name == "mock-specialist":
                quality = 0.8 if task == TaskCategory.CODE else 0.4
            capabilities.append(
                CapabilityBenchmark(
                    task=task,
                    provenance=BenchmarkProvenance.SYNTHETIC,
                    quality=quality,
                    latency_ms=latency,
                    benchmark_version=FIXTURE_VERSION,
                )
            )
        models.append(
            ModelManifest(
                id=name,
                source=ManifestSource(uri=f"https://example.invalid/fixtures/{name}"),
                runtime_tag=f"{name}:fixture",
                family="invented-routing-fixture",
                quantization="fixture-q4",
                capability_benchmark=tuple(capabilities),
            )
        )
    return ModelCatalog(models=tuple(models))
