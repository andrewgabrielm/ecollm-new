"""Loopback API boundary, exact origins/hosts and environment-only write authorization."""

import asyncio
import ipaddress
import re
import secrets
from collections.abc import Mapping
from os import environ
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import Field, SecretStr, StrictInt, model_validator
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.api.schemas import ErrorResponse
from backend.contracts import ContractModel, ErrorCode

MAX_UPLOAD_SECONDS = 10.0


class ApiSettings(ContractModel):
    host: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    port: StrictInt = Field(default=8000, ge=1024, le=65535)
    write_token: SecretStr = Field(repr=False, exclude=True)
    allowed_origins: tuple[str, ...] = ()
    max_body_bytes: StrictInt = Field(default=65536, ge=1024, le=65536)

    @model_validator(mode="after")
    def validate_boundary(self) -> Self:
        token = self.write_token.get_secret_value()
        if not 32 <= len(token) <= 256 or re.fullmatch(r"[A-Za-z0-9_-]+", token) is None:
            raise ValueError("LOCAL_WRITE_TOKEN must be 32–256 URL-safe ASCII characters")
        for origin in self.allowed_origins:
            try:
                parsed = urlsplit(origin)
                valid = (
                    parsed.scheme == "http"
                    and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
                    and parsed.path == ""
                    and not parsed.query
                    and not parsed.fragment
                    and parsed.username is None
                    and parsed.password is None
                    and parsed.port is not None
                    and 1024 <= parsed.port <= 65535
                )
                host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
                valid = valid and origin == f"http://{host}:{parsed.port}"
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Allowed origins must be exact local HTTP origins")
        return self

    @property
    def hosts(self) -> frozenset[str]:
        return frozenset(f"{host}:{self.port}" for host in ("127.0.0.1", "localhost", "[::1]"))

    @property
    def origins(self) -> tuple[str, ...]:
        return tuple(sorted({*(f"http://{host}" for host in self.hosts), *self.allowed_origins}))

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        port: int = 8000,
        host: Literal["127.0.0.1", "::1"] = "127.0.0.1",
    ) -> "ApiSettings":
        source = environ if environment is None else environment
        return cls(host=host, port=port, write_token=SecretStr(source.get("LOCAL_WRITE_TOKEN", "")))


class LocalApiBoundary:
    def __init__(self, app: ASGIApp, settings: ApiSettings) -> None:
        self.app = app
        self.settings = settings

    async def _deny(
        self, scope: Scope, receive: Receive, send: Send, status: int, code: ErrorCode
    ) -> None:
        response = JSONResponse(
            ErrorResponse(request_id=uuid4(), error_code=code).model_dump(
                mode="json", exclude_none=True
            ),
            status_code=status,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: dict[bytes, list[bytes]] = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), []).append(value)
        hosts = headers.get(b"host", [])
        origins = headers.get(b"origin", [])
        if len(hosts) != 1 or hosts[0].decode("latin1").lower() not in self.settings.hosts:
            await self._deny(scope, receive, send, 400, ErrorCode.INVALID_REQUEST)
            return
        try:
            client = scope.get("client")
            local_client = client is not None and ipaddress.ip_address(client[0]).is_loopback
        except ValueError:
            local_client = False
        if (
            not local_client
            or len(origins) > 1
            or (origins and origins[0].decode("latin1") not in self.settings.origins)
        ):
            await self._deny(scope, receive, send, 403, ErrorCode.POLICY_BLOCKED)
            return
        method = scope["method"]
        body: bytes | None = None
        if method not in {"GET", "HEAD", "OPTIONS"}:
            tokens = headers.get(b"x-local-token", [])
            supplied = tokens[0].decode("latin1") if len(tokens) == 1 else ""
            if (
                not supplied.isascii()
                or not 32 <= len(supplied) <= 256
                or not secrets.compare_digest(
                    supplied, self.settings.write_token.get_secret_value()
                )
            ):
                await self._deny(scope, receive, send, 403, ErrorCode.POLICY_BLOCKED)
                return
            types = headers.get(b"content-type", [])
            has_body = method not in {"DELETE"}
            if has_body and (
                len(types) != 1 or types[0].split(b";", 1)[0].strip().lower() != b"application/json"
            ):
                await self._deny(scope, receive, send, 415, ErrorCode.INVALID_REQUEST)
                return
            lengths = headers.get(b"content-length", [])
            if lengths and b"transfer-encoding" in headers:
                await self._deny(scope, receive, send, 400, ErrorCode.INVALID_REQUEST)
                return
            if len(lengths) > 1 or (
                lengths
                and (
                    not lengths[0].isdigit()
                    or len(lengths[0]) > 10
                    or int(lengths[0]) > self.settings.max_body_bytes
                )
            ):
                await self._deny(scope, receive, send, 413, ErrorCode.INVALID_REQUEST)
                return
            buffer = bytearray()
            upload_deadline = asyncio.get_running_loop().time() + MAX_UPLOAD_SECONDS
            while True:
                try:
                    async with asyncio.timeout(
                        max(0.0, upload_deadline - asyncio.get_running_loop().time())
                    ):
                        message = await receive()
                except TimeoutError:
                    await self._deny(scope, receive, send, 408, ErrorCode.INVALID_REQUEST)
                    return
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    await self._deny(scope, receive, send, 400, ErrorCode.INVALID_REQUEST)
                    return
                buffer.extend(message.get("body", b""))
                if len(buffer) > self.settings.max_body_bytes:
                    await self._deny(scope, receive, send, 413, ErrorCode.INVALID_REQUEST)
                    return
                if not message.get("more_body", False):
                    break
            if lengths and int(lengths[0]) != len(buffer):
                await self._deny(scope, receive, send, 400, ErrorCode.INVALID_REQUEST)
                return
            body = bytes(buffer)
        delivered = False
        response_started = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if body is not None and not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def safe_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message["headers"] = [
                    *message.get("headers", []),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                ]
            await send(message)

        try:
            await self.app(scope, bounded_receive, safe_send)
        except Exception:
            if not response_started:
                await self._deny(scope, receive, send, 500, ErrorCode.INTERNAL)
            # Never re-raise content-bearing framework errors into server logs.
