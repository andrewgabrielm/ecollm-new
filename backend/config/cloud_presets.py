"""Known OpenAI-compatible cloud providers offered in the UI.

Endpoints live here rather than being typed in the browser so the common case
needs no URL entry and cannot be mistyped into a destination that still passes
validation. A provider absent from this list is still addable by endpoint.

Verified 2026-11-25 against each provider's own documentation. Model names are
deliberately absent: they change often, and the panel reads the live list from
the provider once a key is supplied.
"""

from backend.contracts import ContractModel


class CloudPreset(ContractModel):
    model_id: str
    display_name: str
    endpoint: str
    health_endpoint: str
    note: str = ""


CLOUD_PRESETS: tuple[CloudPreset, ...] = (
    CloudPreset(
        model_id="groq",
        display_name="Groq",
        endpoint="https://api.groq.com/openai/v1/chat/completions",
        health_endpoint="https://api.groq.com/openai/v1/models",
        note="Fast inference, generous free tier.",
    ),
    CloudPreset(
        model_id="openai",
        display_name="OpenAI",
        endpoint="https://api.openai.com/v1/chat/completions",
        health_endpoint="https://api.openai.com/v1/models",
    ),
    CloudPreset(
        model_id="gemini",
        display_name="Google Gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        health_endpoint="https://generativelanguage.googleapis.com/v1beta/openai/models",
        note="Google's OpenAI-compatibility endpoint.",
    ),
    CloudPreset(
        model_id="claude",
        display_name="Anthropic Claude",
        endpoint="https://api.anthropic.com/v1/chat/completions",
        health_endpoint="https://api.anthropic.com/v1/models",
        note="Anthropic documents this compatibility layer as intended for "
        "testing rather than production; it returns no thinking output.",
    ),
    CloudPreset(
        model_id="deepseek",
        display_name="DeepSeek",
        endpoint="https://api.deepseek.com/v1/chat/completions",
        health_endpoint="https://api.deepseek.com/v1/models",
    ),
    CloudPreset(
        model_id="xai",
        display_name="xAI Grok",
        endpoint="https://api.x.ai/v1/chat/completions",
        health_endpoint="https://api.x.ai/v1/models",
    ),
)
