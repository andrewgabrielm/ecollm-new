"""FastAPI application factory; importing this module starts nothing."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from os import environ
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from backend.api.routes import router, safe_invalid_request
from backend.api.security import ApiSettings, LocalApiBoundary
from backend.broker.orchestrator import (
    ChatOrchestrator,
    attach_cloud_destination,
    build_mock_broker,
    build_real_broker,
)
from backend.cache.exact_cache import ExactCache
from backend.config.loader import load_settings
from backend.profiles.profile_store import ProfileStore
from backend.storage.cloud_destinations import CloudDestinationStore
from backend.storage.connection import LocalDatabase
from backend.storage.conversations import ConversationStore
from backend.telemetry import TelemetryStore


def create_app(
    *,
    broker: ChatOrchestrator | None = None,
    settings: ApiSettings | None = None,
    environment: Mapping[str, str] | None = None,
    db: LocalDatabase | None = None,
) -> FastAPI:
    security = (
        ApiSettings.from_environment(environment)
        if settings is None
        else ApiSettings.model_validate(
            {**settings.model_dump(warnings=False), "write_token": settings.write_token}
        )
    )
    real_requested = broker is None and not load_settings(environment).mock_mode
    if broker is None and not real_requested:
        broker = build_mock_broker()
    service = broker
    profile_store: ProfileStore | None = None
    cache: ExactCache | None = None
    telemetry_store: TelemetryStore | None = None
    conversation_store: ConversationStore | None = None
    destination_store: CloudDestinationStore | None = None
    if db is not None:
        profile_store = ProfileStore(db)
        cache = ExactCache(db)
        telemetry_store = TelemetryStore(db)
        conversation_store = ConversationStore(db)
        destination_store = CloudDestinationStore(db)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = service
        if real_requested:
            source = environ if environment is None else environment
            store = Path(source["MODEL_STORE"]).expanduser() if source.get("MODEL_STORE") else None
            active = await build_real_broker(
                endpoint=source.get("OLLAMA_ENDPOINT") or "http://127.0.0.1:11434",
                model_store=store,
            )
            app.state.broker = active
        # Owner-added destinations are reattached here so they survive a restart;
        # each row is re-validated by the store before it is wired.
        if active is not None and destination_store is not None:
            for saved in destination_store.list():
                try:
                    attach_cloud_destination(active, saved)
                except ValueError:
                    continue
        try:
            yield
        finally:
            if active is not None:
                await active.aclose()

    app = FastAPI(
        title="Adaptive Local-First Broker",
        version="0.1.0",
        debug=False,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
        description=(
            "Single-worker loopback API. Default factory uses synthetic mocks only. "
            "Exact cache opt-in via preferences."
        ),
    )
    app.state.broker = service
    app.state.profile_store = profile_store
    app.state.cache = cache
    app.state.telemetry = telemetry_store
    app.state.conversations = conversation_store
    app.state.cloud_destinations = destination_store
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return safe_invalid_request()

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        response = safe_invalid_request()
        response.status_code = exc.status_code
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(security.origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Local-Token"],
        allow_credentials=False,
    )
    app.add_middleware(LocalApiBoundary, settings=security)
    return app
