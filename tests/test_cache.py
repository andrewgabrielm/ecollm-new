"""Step 10 exact cache and preferences tests; no external services."""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.api.schemas import (
    CacheStatus,
    ChatRequest,
    GenerationOptions,
)
from backend.broker.extensions import Extensions
from backend.broker.orchestrator import ChatOrchestrator, build_mock_broker
from backend.cache.eligibility import cache_eligible_request
from backend.cache.exact_cache import ExactCache
from backend.cache.key import exact_cache_key
from backend.contracts import ExecutionMode, SensitivityScope, TaskCategory
from backend.profiles.preferences import PreferenceProfile
from backend.profiles.profile_store import ProfileStore
from backend.storage.connection import LocalDatabase


@pytest.fixture()
def db(tmp_path: Path) -> LocalDatabase:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    return database


@pytest.fixture()
def cache(db: LocalDatabase) -> ExactCache:
    return ExactCache(db)


@pytest.fixture()
def profile_store(db: LocalDatabase) -> ProfileStore:
    return ProfileStore(db)


class TestCacheKey:
    def test_deterministic(self) -> None:
        opts = GenerationOptions()
        k1 = exact_cache_key(
            prompt="hello",
            task=TaskCategory.QA,
            model_id="mock-general",
            model_version="v1",
            mode=ExecutionMode.LOCAL_ONLY,
            sensitivity_scope=SensitivityScope.NON_SENSITIVE,
            options=opts,
        )
        k2 = exact_cache_key(
            prompt="hello",
            task=TaskCategory.QA,
            model_id="mock-general",
            model_version="v1",
            mode=ExecutionMode.LOCAL_ONLY,
            sensitivity_scope=SensitivityScope.NON_SENSITIVE,
            options=opts,
        )
        assert k1 == k2
        assert len(k1) == 64

    def test_different_prompts_different_keys(self) -> None:
        opts = GenerationOptions()
        k1 = exact_cache_key(
            prompt="hello",
            task=TaskCategory.QA,
            model_id="m",
            model_version="v1",
            mode=ExecutionMode.LOCAL_ONLY,
            sensitivity_scope=SensitivityScope.NON_SENSITIVE,
            options=opts,
        )
        k2 = exact_cache_key(
            prompt="world",
            task=TaskCategory.QA,
            model_id="m",
            model_version="v1",
            mode=ExecutionMode.LOCAL_ONLY,
            sensitivity_scope=SensitivityScope.NON_SENSITIVE,
            options=opts,
        )
        assert k1 != k2

    def test_different_models_different_keys(self) -> None:
        opts = GenerationOptions()
        k1 = exact_cache_key(
            prompt="hello",
            task=TaskCategory.QA,
            model_id="model-a",
            model_version="v1",
            mode=ExecutionMode.LOCAL_ONLY,
            sensitivity_scope=SensitivityScope.NON_SENSITIVE,
            options=opts,
        )
        k2 = exact_cache_key(
            prompt="hello",
            task=TaskCategory.QA,
            model_id="model-b",
            model_version="v1",
            mode=ExecutionMode.LOCAL_ONLY,
            sensitivity_scope=SensitivityScope.NON_SENSITIVE,
            options=opts,
        )
        assert k1 != k2


class TestCacheEligibility:
    def test_eligible_when_all_conditions_met(self) -> None:
        profile = PreferenceProfile(exact_cache_enabled=True)
        assert cache_eligible_request(profile, SensitivityScope.NON_SENSITIVE, 0) is True

    def test_ineligible_when_cache_disabled(self) -> None:
        profile = PreferenceProfile(exact_cache_enabled=False)
        assert cache_eligible_request(profile, SensitivityScope.NON_SENSITIVE, 0) is False

    def test_ineligible_when_confidential(self) -> None:
        profile = PreferenceProfile(exact_cache_enabled=True)
        assert cache_eligible_request(profile, SensitivityScope.CONFIDENTIAL, 0) is False

    def test_ineligible_when_unspecified(self) -> None:
        profile = PreferenceProfile(exact_cache_enabled=True)
        assert cache_eligible_request(profile, SensitivityScope.UNSPECIFIED, 0) is False

    def test_ineligible_when_pii_present(self) -> None:
        profile = PreferenceProfile(exact_cache_enabled=True)
        assert cache_eligible_request(profile, SensitivityScope.NON_SENSITIVE, 1) is False


class TestExactCache:
    def test_store_and_lookup(self, cache: ExactCache) -> None:
        cache.store("key1", "answer", "model-a", "v1", "general")
        hit = cache.lookup("key1")
        assert hit is not None
        assert hit.answer == "answer"
        assert hit.model_id == "model-a"

    def test_miss(self, cache: ExactCache) -> None:
        assert cache.lookup("nonexistent") is None

    def test_count(self, cache: ExactCache) -> None:
        assert cache.count() == 0
        cache.store("key1", "a", "m", "v", "t")
        assert cache.count() == 1
        cache.store("key2", "b", "m", "v", "t")
        assert cache.count() == 2

    def test_delete_all(self, cache: ExactCache) -> None:
        cache.store("key1", "a", "m", "v", "t")
        cache.store("key2", "b", "m", "v", "t")
        deleted = cache.delete_all()
        assert deleted == 2
        assert cache.count() == 0

    def test_eviction_respects_max_entries(self, db: LocalDatabase) -> None:
        small_cache = ExactCache(db, max_entries=3)
        for i in range(5):
            small_cache.store(f"key{i}", f"answer{i}", "m", "v", "t")
        assert small_cache.count() == 3

    def test_invalid_bounds_rejected(self, db: LocalDatabase) -> None:
        with pytest.raises(ValueError):
            ExactCache(db, max_entries=0)
        with pytest.raises(ValueError):
            ExactCache(db, ttl_seconds=0)


class TestProfileStore:
    def test_load_default(self, profile_store: ProfileStore) -> None:
        profile = profile_store.load()
        assert profile.profile_id == "local"
        assert profile.exact_cache_enabled is False

    def test_save_and_load(self, profile_store: ProfileStore) -> None:
        profile = PreferenceProfile(exact_cache_enabled=True)
        profile_store.save(profile)
        loaded = profile_store.load()
        assert loaded.exact_cache_enabled is True

    def test_reset(self, profile_store: ProfileStore) -> None:
        profile = PreferenceProfile(exact_cache_enabled=True)
        profile_store.save(profile)
        default = profile_store.reset()
        assert default.exact_cache_enabled is False
        assert profile_store.load().exact_cache_enabled is False


class TestExtensions:
    def test_cache_enabled_allowed(self) -> None:
        ext = Extensions(cache_enabled=True)
        assert ext.cache_enabled is True

    def test_persistence_still_locked(self) -> None:
        with pytest.raises(ValidationError):
            Extensions.model_validate({"persistence_enabled": True})

    def test_learned_router_still_locked(self) -> None:
        with pytest.raises(ValidationError):
            Extensions.model_validate({"learned_router_enabled": True})


def _mock_broker_with_cache(tmp_path: Path) -> tuple[ChatOrchestrator, ExactCache]:
    broker = build_mock_broker()
    db = LocalDatabase(tmp_path / "var")
    db.migrate()
    ec = ExactCache(db)
    broker._cache = ec
    broker._preferences = PreferenceProfile(exact_cache_enabled=True)
    broker.extensions = Extensions(cache_enabled=True)
    return broker, ec


class TestCacheIntegration:
    def test_cache_hit_returns_cached_answer(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            broker, ec = _mock_broker_with_cache(tmp_path)
            request = ChatRequest(
                prompt="What is 2+2?",
                sensitivity_scope=SensitivityScope.NON_SENSITIVE,
            )
            r1 = await broker.chat(request)
            assert r1.status == "success"
            assert r1.cache_status == CacheStatus.MISS
            assert ec.count() == 1

            r2 = await broker.chat(request)
            assert r2.status == "success"
            assert r2.cache_status == CacheStatus.EXACT_HIT
            assert r2.answer == r1.answer
            assert r2.cache_provenance is not None
            assert r2.cache_provenance.model_id == r1.model_id

        asyncio.run(scenario())

    def test_cache_disabled_returns_disabled_status(self) -> None:
        async def scenario() -> None:
            broker = build_mock_broker()
            request = ChatRequest(prompt="hello")
            result = await broker.chat(request)
            assert result.status == "success"
            assert result.cache_status == CacheStatus.DISABLED

        asyncio.run(scenario())

    def test_cache_bypass_for_confidential_request(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            broker, ec = _mock_broker_with_cache(tmp_path)
            request = ChatRequest(
                prompt="Tell me about the secret project",
                sensitivity_scope=SensitivityScope.CONFIDENTIAL,
            )
            result = await broker.chat(request)
            assert result.status == "success"
            assert result.cache_status == CacheStatus.DISABLED
            assert ec.count() == 0

        asyncio.run(scenario())

    def test_metrics_reflect_cache_state(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            broker, ec = _mock_broker_with_cache(tmp_path)
            metrics = broker.metrics_status()
            assert metrics.cache_enabled is True
            assert metrics.cache_entries == 0
            assert metrics.cache_hits == 0

            request = ChatRequest(
                prompt="hello world",
                sensitivity_scope=SensitivityScope.NON_SENSITIVE,
            )
            await broker.chat(request)
            await broker.chat(request)
            metrics = broker.metrics_status()
            assert metrics.cache_entries == 1
            assert metrics.cache_hits == 1

        asyncio.run(scenario())

    def test_update_preferences_enables_cache(self) -> None:
        broker = build_mock_broker()
        assert broker.extensions.cache_enabled is False
        broker.update_preferences(PreferenceProfile(exact_cache_enabled=True))
        assert broker.extensions.cache_enabled is True
        assert broker.preferences.exact_cache_enabled is True
