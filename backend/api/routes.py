"""API routes; chat broker, preferences and cache management."""

from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Security
from fastapi.security import APIKeyHeader
from starlette.responses import JSONResponse

from backend.api.schemas import (
    ApprovalRequest,
    ApprovalRequiredResponse,
    CacheClearedResponse,
    CancelledResponse,
    CatalogModel,
    CatalogResponse,
    ChatRequest,
    ChatResponse,
    CloudApiKeyUpdate,
    CloudDestinationCreate,
    CloudDestinationInfo,
    CloudDestinationsResponse,
    CloudModelOptions,
    CloudModelUpdate,
    CloudPresetInfo,
    CloudPresetsResponse,
    ConversationDetail,
    ConversationListResponse,
    ConversationMessage,
    ConversationSaveRequest,
    ConversationSummary,
    ErrorResponse,
    HealthResponse,
    MetricsResponse,
    PreferencesResponse,
    PreferencesUpdateRequest,
)
from backend.broker.orchestrator import (
    ChatOrchestrator,
    ChatOutcome,
    attach_cloud_destination,
    detach_cloud_destination,
)
from backend.config.cloud_presets import CLOUD_PRESETS
from backend.config.loader import CloudDestinationConfig
from backend.contracts import ErrorCode
from backend.profiles.preferences import PreferenceProfile
from backend.storage.cloud_destinations import CloudDestinationStore
from backend.storage.connection import StorageError
from backend.storage.conversations import ConversationStore, SavedMessage
from backend.telemetry import TelemetryRecorder, TelemetryStore, aggregate
from backend.telemetry.types import TelemetrySummary

router = APIRouter(prefix="/api")
write_key = APIKeyHeader(
    name="X-Local-Token", auto_error=False, description="Environment-only local write authorization"
)


def _broker(request: Request) -> ChatOrchestrator:
    return cast(ChatOrchestrator, request.app.state.broker)


def _render(result: ChatOutcome) -> JSONResponse:
    status = 202 if isinstance(result, ApprovalRequiredResponse) else 200
    if isinstance(result, ErrorResponse):
        status = {
            ErrorCode.INVALID_REQUEST: 422,
            ErrorCode.UNAVAILABLE: 503,
            ErrorCode.TIMEOUT: 504,
            ErrorCode.POLICY_BLOCKED: 403,
            ErrorCode.APPROVAL_CONFLICT: 409,
            ErrorCode.VALIDATION_FAILED: 502,
            ErrorCode.PRIVACY_FAILED: 503,
            ErrorCode.OUTPUT_BLOCKED: 502,
        }.get(result.error_code, 500)
    return JSONResponse(result.model_dump(mode="json", exclude_none=True), status_code=status)


_responses: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (403, 409, 422, 500, 502, 503, 504)
}


@router.post(
    "/chat",
    response_model=ChatResponse | ApprovalRequiredResponse | ErrorResponse,
    responses={**_responses, 202: {"model": ApprovalRequiredResponse}},
)
async def chat(
    body: ChatRequest, request: Request, _token: Annotated[str | None, Security(write_key)] = None
) -> JSONResponse:
    broker = _broker(request)
    outcome = await broker.chat(body)
    _record_telemetry(request, outcome, broker)
    return _render(outcome)


@router.post(
    "/chat/{request_id}/approval",
    response_model=ChatResponse | CancelledResponse | ErrorResponse,
    responses=_responses,
)
async def approval(
    request_id: UUID,
    body: ApprovalRequest,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> JSONResponse:
    broker = _broker(request)
    outcome = await broker.approval(request_id, body)
    _record_telemetry(request, outcome, broker)
    return _render(outcome)


@router.get("/health", response_model=HealthResponse)
async def health_status(request: Request) -> HealthResponse:
    return _broker(request).health_status()


@router.get("/metrics", response_model=MetricsResponse)
async def metrics_status(request: Request) -> MetricsResponse:
    return _broker(request).metrics_status()


def _preferences_response(profile: PreferenceProfile) -> PreferencesResponse:
    return PreferencesResponse(
        exact_cache_enabled=profile.exact_cache_enabled,
        persistent_analytics_enabled=profile.persistent_analytics_enabled,
        local_learning_enabled=profile.local_learning_enabled,
        workload_persistence_enabled=profile.workload_persistence_enabled,
        mode=profile.mode,
    )


@router.get("/preferences", response_model=PreferencesResponse)
async def get_preferences(request: Request) -> PreferencesResponse:
    return _preferences_response(_broker(request).preferences)


@router.put("/preferences", response_model=PreferencesResponse)
async def update_preferences(
    body: PreferencesUpdateRequest,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> PreferencesResponse:
    broker = _broker(request)
    profile_store = getattr(request.app.state, "profile_store", None)
    current = broker.preferences
    updates = body.model_dump(exclude_none=True)
    new_profile = current.model_copy(update=updates)
    broker.update_preferences(new_profile)
    if profile_store is not None:
        profile_store.save(new_profile)
    if not new_profile.persistent_analytics_enabled:
        store = _telemetry_store(request)
        if store is not None:
            store.delete_all()
    # Opting out of saved chats purges what was already written, so stored text
    # never outlives the consent that allowed it.
    if not new_profile.workload_persistence_enabled:
        conversations = _conversation_store(request)
        if conversations is not None:
            conversations.delete_all()
    return _preferences_response(new_profile)


@router.post("/preferences/reset", response_model=PreferencesResponse)
async def reset_preferences(
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> PreferencesResponse:
    broker = _broker(request)
    profile_store = getattr(request.app.state, "profile_store", None)
    defaults = PreferenceProfile()
    broker.update_preferences(defaults)
    if profile_store is not None:
        profile_store.save(defaults)
    store = _telemetry_store(request)
    if store is not None:
        store.delete_all()
    return _preferences_response(defaults)


@router.get("/catalog", response_model=CatalogResponse)
async def catalog(request: Request) -> CatalogResponse:
    snapshot = _broker(request).registry.snapshot()
    status_by_id = {status.model_id: status for status in snapshot.catalog_statuses}
    rows = tuple(
        CatalogModel(
            model_id=entry.manifest.id,
            manifest_version=entry.manifest.version,
            family=entry.manifest.family,
            quantization=entry.manifest.quantization,
            verified=status_by_id[entry.manifest.id].verified,
            installed=status_by_id[entry.manifest.id].installed is True,
            resident=status_by_id[entry.manifest.id].resident,
            eligible=status_by_id[entry.manifest.id].eligible,
        )
        for entry in snapshot.roster.entries
        if entry.manifest.id in status_by_id
    )
    return CatalogResponse(
        models=rows,
        hardware_known=snapshot.hardware.platform is not None,
        mock_mode=snapshot.mock_mode,
    )


@router.delete("/cache", response_model=CacheClearedResponse)
async def clear_cache(
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CacheClearedResponse:
    broker = _broker(request)
    cache = broker.cache
    deleted = cache.delete_all() if cache is not None else 0
    return CacheClearedResponse(deleted=deleted)


def _conversation_store(request: Request) -> ConversationStore | None:
    store = getattr(request.app.state, "conversations", None)
    if isinstance(store, ConversationStore):
        return store
    return None


def _saving_enabled(request: Request) -> bool:
    return _broker(request).preferences.workload_persistence_enabled


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(request: Request) -> ConversationListResponse:
    enabled = _saving_enabled(request)
    store = _conversation_store(request)
    if store is None or not enabled:
        return ConversationListResponse(enabled=enabled)
    return ConversationListResponse(
        enabled=True,
        conversations=tuple(
            ConversationSummary(
                id=item.id,
                title=item.title,
                created_at=item.created_at,
                updated_at=item.updated_at,
                message_count=item.message_count,
            )
            for item in store.list()
        ),
    )


@router.put("/conversations", response_model=ConversationListResponse)
async def save_conversation(
    body: ConversationSaveRequest,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> ConversationListResponse:
    enabled = _saving_enabled(request)
    store = _conversation_store(request)
    # Saving is refused rather than silently skipped while the preference is
    # off, so a client cannot believe it persisted a conversation that was not.
    if store is None or not enabled:
        return ConversationListResponse(enabled=enabled)
    try:
        store.save(
            body.id,
            tuple(
                SavedMessage(role=item.role, content=item.content, model_id=item.model_id)
                for item in body.messages
            ),
        )
    except (ValueError, StorageError):
        # Bounds are already enforced by the request schema, so this is a
        # storage-level refusal. Return the current list rather than naming the
        # cause; the absent conversation is itself the signal.
        return await list_conversations(request)
    return await list_conversations(request)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def read_conversation(conversation_id: UUID, request: Request) -> ConversationDetail:
    store = _conversation_store(request)
    if store is None or not _saving_enabled(request):
        return ConversationDetail(id=conversation_id)
    return ConversationDetail(
        id=conversation_id,
        messages=tuple(
            ConversationMessage(role=item.role, content=item.content, model_id=item.model_id)
            for item in store.messages(conversation_id)
        ),
    )


@router.delete("/conversations/{conversation_id}", response_model=CacheClearedResponse)
async def delete_conversation(
    conversation_id: UUID,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CacheClearedResponse:
    store = _conversation_store(request)
    # Deletion is always honoured, including after the preference is turned off,
    # so stored text can never outlive consent.
    if store is None:
        return CacheClearedResponse(deleted=0)
    return CacheClearedResponse(deleted=store.delete(conversation_id))


@router.delete("/conversations", response_model=CacheClearedResponse)
async def delete_all_conversations(
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CacheClearedResponse:
    store = _conversation_store(request)
    if store is None:
        return CacheClearedResponse(deleted=0)
    return CacheClearedResponse(deleted=store.delete_all())


def _telemetry_store(request: Request) -> TelemetryStore | None:
    store = getattr(request.app.state, "telemetry", None)
    if isinstance(store, TelemetryStore):
        return store
    return None


def _record_telemetry(request: Request, outcome: ChatOutcome, broker: ChatOrchestrator) -> None:
    store = _telemetry_store(request)
    if store is None:
        return
    recorder = TelemetryRecorder(store, enabled=broker.preferences.persistent_analytics_enabled)
    if not recorder.enabled:
        return
    if isinstance(outcome, ChatResponse):
        recorder.record_chat_response(outcome)
    elif isinstance(outcome, ErrorResponse):
        recorder.record_error_response(
            outcome,
            task=None,
            router_type=broker.router.router_type,
            router_version=broker.router.router_version,
        )


@router.get("/telemetry", response_model=TelemetrySummary)
async def telemetry_summary(request: Request) -> TelemetrySummary:
    broker = _broker(request)
    enabled = broker.preferences.persistent_analytics_enabled
    store = _telemetry_store(request)
    if store is None:
        return aggregate([], enabled=enabled, retention_days=30)
    return aggregate(
        store.iter_rows() if enabled else [],
        enabled=enabled,
        retention_days=store.retention_days,
    )


@router.delete("/telemetry", response_model=CacheClearedResponse)
async def clear_telemetry(
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CacheClearedResponse:
    store = _telemetry_store(request)
    deleted = store.delete_all() if store is not None else 0
    return CacheClearedResponse(deleted=deleted)


def _cloud_destinations(broker: ChatOrchestrator) -> tuple[CloudDestinationInfo, ...]:
    rows: list[CloudDestinationInfo] = []
    for model_id, provider in broker.cloud_providers().items():
        destination = provider.destination
        rows.append(
            CloudDestinationInfo(
                model_id=destination.model_id,
                model_version=destination.model_version,
                display_name=destination.display_name,
                endpoint=destination.endpoint,
                runtime_model=provider.runtime_model,
                key_available=provider.describe(model_id).key_available,
                key_source=provider.key_source,
            )
        )
    return tuple(rows)


@router.get("/cloud", response_model=CloudDestinationsResponse)
async def cloud_destinations(request: Request) -> CloudDestinationsResponse:
    return CloudDestinationsResponse(destinations=_cloud_destinations(_broker(request)))


@router.put("/cloud/{model_id}/key", response_model=CloudDestinationsResponse)
async def set_cloud_key(
    model_id: str,
    body: CloudApiKeyUpdate,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CloudDestinationsResponse:
    broker = _broker(request)
    providers = broker.cloud_providers()
    provider = providers.get(model_id)
    if provider is None:
        return _not_found_cloud(request)
    try:
        provider.set_api_key(body.api_key)
    except ValueError:
        return _not_found_cloud(request)
    return CloudDestinationsResponse(destinations=_cloud_destinations(broker))


def _destination_store(request: Request) -> CloudDestinationStore | None:
    store = getattr(request.app.state, "cloud_destinations", None)
    if isinstance(store, CloudDestinationStore):
        return store
    return None


@router.get("/cloud/presets", response_model=CloudPresetsResponse)
async def cloud_presets(request: Request) -> CloudPresetsResponse:
    """Known providers the owner can add without typing an endpoint.

    Already-registered providers are omitted so the picker cannot offer a
    duplicate that POST /api/cloud would then refuse.
    """
    taken = set(_broker(request).cloud_providers())
    return CloudPresetsResponse(
        presets=tuple(
            CloudPresetInfo(
                model_id=item.model_id,
                display_name=item.display_name,
                endpoint=item.endpoint,
                health_endpoint=item.health_endpoint,
                note=item.note,
            )
            for item in CLOUD_PRESETS
            if item.model_id not in taken
        )
    )


@router.post("/cloud", response_model=CloudDestinationsResponse)
async def add_cloud_destination(
    body: CloudDestinationCreate,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CloudDestinationsResponse:
    """Register a new OpenAI-compatible destination.

    Adding one widens where sanitized text may be sent, so it needs the local
    write token and the same https/host validation the packaged entries get.
    Nothing is sent anywhere until a key is supplied and the mode allows cloud.
    """
    broker = _broker(request)
    store = _destination_store(request)
    if body.model_id in broker.cloud_providers():
        return _not_found_cloud(request)
    config = CloudDestinationConfig.model_validate(
        {**body.model_dump(), "model_version": "owner-v1"}
    )
    try:
        if store is not None:
            store.add(config)
        attach_cloud_destination(broker, config)
    except (ValueError, StorageError):
        if store is not None:
            store.remove(body.model_id)
        return _not_found_cloud(request)
    return CloudDestinationsResponse(destinations=_cloud_destinations(broker))


@router.delete("/cloud/{model_id}", response_model=CloudDestinationsResponse)
async def remove_cloud_destination(
    model_id: str,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CloudDestinationsResponse:
    broker = _broker(request)
    store = _destination_store(request)
    # Only owner-added destinations are removable; packaged ones stay declared.
    if store is None or store.remove(model_id) == 0:
        return _not_found_cloud(request)
    await detach_cloud_destination(broker, model_id)
    return CloudDestinationsResponse(destinations=_cloud_destinations(broker))


@router.get("/cloud/{model_id}/models", response_model=CloudModelOptions)
async def list_cloud_models(
    model_id: str,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CloudModelOptions:
    """Ask the destination which models this key can reach.

    Reaches the owner-declared destination carrying only the key, so it is a
    write-authorized route even though it reads: it spends the credential.
    """
    broker = _broker(request)
    provider = broker.cloud_providers().get(model_id)
    if provider is None:
        return CloudModelOptions(model_id=model_id)
    names = await provider.available_models(model_id)
    return CloudModelOptions(
        model_id=model_id,
        runtime_model=provider.runtime_model,
        models=names,
        reachable=bool(names),
    )


@router.put("/cloud/{model_id}/model", response_model=CloudDestinationsResponse)
async def set_cloud_model(
    model_id: str,
    body: CloudModelUpdate,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CloudDestinationsResponse:
    broker = _broker(request)
    providers = broker.cloud_providers()
    provider = providers.get(model_id)
    if provider is None:
        return _not_found_cloud(request)
    try:
        provider.set_runtime_model(body.runtime_model)
    except ValueError:
        return _not_found_cloud(request)
    return CloudDestinationsResponse(destinations=_cloud_destinations(broker))


@router.delete("/cloud/{model_id}/key", response_model=CloudDestinationsResponse)
async def clear_cloud_key(
    model_id: str,
    request: Request,
    _token: Annotated[str | None, Security(write_key)] = None,
) -> CloudDestinationsResponse:
    broker = _broker(request)
    providers = broker.cloud_providers()
    provider = providers.get(model_id)
    if provider is None:
        return _not_found_cloud(request)
    provider.clear_api_key()
    return CloudDestinationsResponse(destinations=_cloud_destinations(broker))


def _not_found_cloud(request: Request) -> CloudDestinationsResponse:
    # Unknown model_id or malformed payload: do not reveal which; return the
    # current cloud destinations list so the UI can refresh its state.
    return CloudDestinationsResponse(destinations=_cloud_destinations(_broker(request)))


def safe_invalid_request() -> JSONResponse:
    return _render(ErrorResponse(request_id=uuid4(), error_code=ErrorCode.INVALID_REQUEST))
