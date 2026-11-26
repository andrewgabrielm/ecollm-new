"""Internal transport helpers; never surface upstream bodies or exception text."""

import math
import re
from time import perf_counter

import httpx

from backend.config.endpoints import validate_endpoint as validate_endpoint
from backend.contracts import ErrorCode
from backend.providers.base import ProviderFailure


class ProviderTransportError(RuntimeError):
    def __init__(self, code: ErrorCode, *, retryable_connection: bool = False) -> None:
        self.code = code
        self.retryable_connection = retryable_connection
        super().__init__("Provider metadata operation failed")


def elapsed_ms(start: float) -> float:
    return max(0.0, (perf_counter() - start) * 1000)


def valid_timeout(value: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 < value <= 120
    )


def validate_model_ids(model_ids: object) -> None:
    if not isinstance(model_ids, (tuple, list)) or not model_ids:
        raise ValueError("At least one configured model is required")
    for model_id in model_ids:
        if (
            not isinstance(model_id, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", model_id) is None
        ):
            raise ValueError("Invalid configured model ID")


def create_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """One client per adapter, zero connection retries and no proxy/env inheritance."""
    return httpx.AsyncClient(
        transport=transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        trust_env=False,
        follow_redirects=False,
        timeout=30,
    )


def status_error(response: httpx.Response) -> ErrorCode | None:
    if response.status_code == 200:
        return None
    if response.status_code in {401, 403} or 300 <= response.status_code < 400:
        return ErrorCode.POLICY_BLOCKED
    if response.status_code in {400, 422}:
        return ErrorCode.INVALID_REQUEST
    return ErrorCode.UNAVAILABLE


def failure(
    model_id: str,
    version: str,
    code: ErrorCode,
    start: float,
    *,
    retryable_connection: bool = False,
) -> ProviderFailure:
    return ProviderFailure(
        model_id=model_id,
        model_version=version,
        error_code=code,
        latency_ms=elapsed_ms(start),
        retryable_connection=retryable_connection,
    )
