"""Domain-separated canonical version identifiers for JSON-compatible contracts."""

import hashlib
import json
import math
from collections.abc import Mapping
from typing import TypeAlias

SCHEMA_VERSION = "contracts-v1"
POLICY_VERSION = "policy-v1"
ARTIFACT_VERSION = "artifact-v1"

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]


def _canonical_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Canonical values must be finite")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Canonical keys must be strings")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    raise ValueError("Unsupported canonical value type")


def canonical_hash(value: object, *, namespace: str = SCHEMA_VERSION) -> str:
    """Preserve text/array order and JSON types; exclude mapping insertion order."""
    if not namespace or "\0" in namespace:
        raise ValueError("Invalid version namespace")
    encoded = json.dumps(
        _canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(namespace.encode("utf-8") + b"\0" + encoded).hexdigest()
