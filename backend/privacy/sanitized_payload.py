"""Immutable handoff with a process-local seal issued after successful sanitization.

Structural deserialization alone is never trusted. The private issuer belongs only
to the sanitizer; later dispatch still requires the policy gate's authorization.
"""

import hmac
import secrets
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, PrivateAttr, model_validator

from backend.api.schemas import PrivacyTypeCount
from backend.contracts import ContractModel
from backend.versions import canonical_hash

_ISSUANCE_KEY = secrets.token_bytes(32)


class SanitizationProvenance(ContractModel):
    sanitizer_version: str = Field(min_length=1, max_length=128)
    analyzer_version: str = Field(min_length=1, max_length=128)
    status: Literal["sanitized"] = "sanitized"


def sanitized_digest(
    request_id: UUID,
    text: str,
    provenance: SanitizationProvenance,
    privacy_counts: tuple[PrivacyTypeCount, ...] = (),
) -> str:
    """Bind request, exact text, scanner provenance and detected policy-sensitive types."""
    return canonical_hash(
        {
            "request_id": str(request_id),
            "text": text,
            "provenance": provenance.model_dump(mode="json"),
            "privacy_counts": [count.model_dump(mode="json") for count in privacy_counts],
        },
        namespace="sanitized-payload-v2",
    )


class SanitizedPayload(ContractModel):
    _issuance_seal: bytes = PrivateAttr(default=b"")
    request_id: UUID
    text: str = Field(min_length=1, max_length=64000, repr=False)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance: SanitizationProvenance
    privacy_counts: tuple[PrivacyTypeCount, ...] = ()

    @property
    def is_trusted(self) -> bool:
        """Reject deserialized values and unchecked copy/update tampering."""
        try:
            expected_digest = sanitized_digest(
                self.request_id, self.text, self.provenance, self.privacy_counts
            )
            return self.digest == expected_digest and hmac.compare_digest(
                self._issuance_seal,
                hmac.digest(_ISSUANCE_KEY, expected_digest.encode("ascii"), "sha256"),
            )
        except (AttributeError, TypeError, ValueError):
            return False

    @model_validator(mode="after")
    def validate_digest(self) -> Self:
        if len({count.entity_type for count in self.privacy_counts}) != len(self.privacy_counts):
            raise ValueError("Duplicate privacy type counts")
        if self.digest != sanitized_digest(
            self.request_id, self.text, self.provenance, self.privacy_counts
        ):
            raise ValueError("sanitized payload digest mismatch")
        return self


def _issue_sanitized_payload(
    request_id: UUID,
    text: str,
    provenance: SanitizationProvenance,
    privacy_counts: tuple[PrivacyTypeCount, ...] = (),
) -> SanitizedPayload:
    """Internal sanitizer boundary; callers must have completed scanning and masking."""
    digest = sanitized_digest(request_id, text, provenance, privacy_counts)
    payload = SanitizedPayload(
        request_id=request_id,
        text=text,
        digest=digest,
        provenance=provenance,
        privacy_counts=privacy_counts,
    )
    object.__setattr__(
        payload,
        "__pydantic_private__",
        {"_issuance_seal": hmac.digest(_ISSUANCE_KEY, digest.encode("ascii"), "sha256")},
    )
    return payload
