"""Real local service lifecycle and metadata/resource checks; no direct generation."""

import asyncio
from typing import Final, Protocol

from backend.broker.chat import ChatOrchestrator
from backend.model_management.hardware import (
    HardwareProbe,
    HardwareReport,
    memory_budget,
    resident_limit,
)
from backend.model_management.initial_install import RuntimeLease
from backend.model_management.manifest import ModelCatalog
from backend.model_management.registry import ModelRegistry
from backend.model_management.runtime_report import RuntimeReport

# Ollama silently truncates anything past num_ctx, so a 2048-token window drops
# the start of a conversation without any error. The composed budget in
# backend/api/schemas.py is sized to fit inside this with the output reserve.
# Estimated KV-cache cost for the catalog's 1.5B models is ~28 KiB/token, so
# this is roughly +170 MiB over 2048 — inside the catalog's conservative 2 GiB
# per-model estimate against ~1.35 GiB measured. That is arithmetic, not a
# measurement: confirm with `make feasibility` before trusting it on new hardware.
REAL_CONTEXT_TOKENS: Final = 8192


class LocalMetadataControl(Protocol):
    async def observe(self) -> RuntimeReport: ...
    async def aclose(self) -> None: ...


class LocalResourceMonitor:
    def __init__(
        self,
        control: LocalMetadataControl,
        registry: ModelRegistry,
        catalog: ModelCatalog,
        probe: HardwareProbe,
        hardware: HardwareReport,
        runtime: RuntimeReport,
    ) -> None:
        self.control = control
        self.registry = registry
        self.catalog = catalog
        self.probe = probe
        self.hardware = hardware
        self.runtime = runtime
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    async def refresh(self) -> None:
        async with self._lock:
            try:
                runtime = await self.control.observe()
                hardware = await asyncio.to_thread(self.probe)
                self.runtime = RuntimeReport.model_validate(runtime.model_dump(warnings=False))
                self.hardware = HardwareReport.model_validate(hardware.model_dump(warnings=False))
                self.registry.reconcile(self.runtime.inventory)
            except Exception:
                self.runtime = RuntimeReport()
                self.hardware = HardwareReport()
                self.registry.reconcile(self.runtime.inventory)

    async def permitted(self, model_id: str) -> bool:
        await self.refresh()
        if (
            not self.runtime.available
            or self.hardware.evidence != "observed"
            or self.hardware.context.version != self.registry.hardware.version
        ):
            return False
        eligible = self.registry.snapshot().eligible_model_ids
        if (
            model_id not in eligible
            or self.hardware.available_ram_bytes is None
            or self.hardware.pressure != "normal"
        ):
            return False
        manifest = next(item for item in self.catalog.models if item.id == model_id)
        models = tuple(item for item in self.catalog.models if item.id in eligible)
        limit = resident_limit(models, self.hardware)
        current = next(
            (
                item
                for item in self.runtime.inventory.models
                if item.runtime_tag == manifest.runtime_tag
            ),
            None,
        )
        if (
            self.runtime.resident_count is None
            or self.runtime.resident_bytes is None
            or current is None
            or current.resident is None
        ):
            return False
        required = 0 if current.resident else manifest.memory_bytes
        if required is None or required > max(0, self.hardware.available_ram_bytes - 1024**3):
            return False
        if self.runtime.resident_count > limit:
            return False
        if not current.resident and self.runtime.resident_count >= limit:
            return False
        budget = memory_budget(self.hardware)
        return budget is not None and self.runtime.resident_bytes + required <= budget

    def start(self) -> None:
        async def poll() -> None:
            while True:
                await asyncio.sleep(10)
                try:
                    await self.refresh()
                except Exception:
                    self.registry.reconcile(RuntimeReport().inventory)

        self._task = asyncio.create_task(poll())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


class RealLocalBroker(ChatOrchestrator):
    monitor: LocalResourceMonitor
    runtime_lease: RuntimeLease

    async def aclose(self) -> None:
        try:
            await self.monitor.stop()
            await super().aclose()
        finally:
            try:
                await self.monitor.control.aclose()
            finally:
                self.runtime_lease.release()
