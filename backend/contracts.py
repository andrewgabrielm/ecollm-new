"""Shared input rules and finite vocabularies, without runtime side effects."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ExecutionMode(StrEnum):
    LOCAL_ONLY = "local_only"
    ASK_BEFORE_CLOUD = "ask_before_cloud"
    AUTO_CLOUD = "auto_cloud"
    # Skips the local cascade entirely. Not a fallback mode: the owner has
    # chosen cloud as the primary route for these requests.
    CLOUD_ONLY = "cloud_only"


class SensitivityScope(StrEnum):
    UNSPECIFIED = "unspecified"
    NON_SENSITIVE = "non_sensitive"
    CONFIDENTIAL = "confidential"


class TaskCategory(StrEnum):
    QA = "qa"
    SUMMARISE = "summarise"
    EXTRACT = "extract"
    CLASSIFY = "classify"
    REASON = "reason"
    FORMAT = "format"
    CODE = "code"


class ErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    POLICY_BLOCKED = "policy_blocked"
    APPROVAL_CONFLICT = "approval_conflict"
    INSUFFICIENT_DISK = "insufficient_disk"
    INTERNAL = "internal"
    INVALID_REQUEST = "invalid_request"
    INVALID_CONFIG = "invalid_config"
    VALIDATION_FAILED = "validation_failed"
    PRIVACY_FAILED = "privacy_failed"
    OUTPUT_BLOCKED = "output_blocked"
