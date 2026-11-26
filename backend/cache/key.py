"""Canonical, deterministic cache key for exact match lookup."""

from hashlib import sha256

from backend.api.schemas import GenerationOptions
from backend.broker.validators import ResponseConstraints
from backend.contracts import ExecutionMode, SensitivityScope, TaskCategory
from backend.versions import POLICY_VERSION, SCHEMA_VERSION


def exact_cache_key(
    *,
    prompt: str,
    task: TaskCategory,
    model_id: str,
    model_version: str,
    mode: ExecutionMode,
    sensitivity_scope: SensitivityScope,
    options: GenerationOptions,
    constraints: ResponseConstraints | None = None,
) -> str:
    """Constraints are part of the key because they now change the prompt.

    Before they reached the model the same question with a different requested
    shape produced the same text, so one entry served both. Now it does not,
    and omitting them would return an answer generated under a different shape.
    """

    parts = [
        SCHEMA_VERSION,
        POLICY_VERSION,
        prompt,
        task.value,
        model_id,
        model_version,
        mode.value,
        sensitivity_scope.value,
        str(options.temperature),
        str(options.max_output_tokens),
        str(options.seed) if options.seed is not None else "",
        (constraints or ResponseConstraints()).model_dump_json(),
    ]
    return sha256("\x00".join(parts).encode("utf-8")).hexdigest()
