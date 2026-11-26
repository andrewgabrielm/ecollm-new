"""Cache eligibility: consent, sensitivity scope and PII checks."""

from backend.contracts import SensitivityScope
from backend.profiles.preferences import PreferenceProfile


def cache_eligible_request(
    preferences: PreferenceProfile,
    sensitivity_scope: SensitivityScope,
    pii_count: int,
    *,
    has_history: bool = False,
) -> bool:
    if not preferences.exact_cache_enabled:
        return False
    if sensitivity_scope != SensitivityScope.NON_SENSITIVE:
        return False
    # The key is built from the prompt alone, so "summarise that" in one
    # conversation would hit an entry stored for the same words in another.
    # Conversational turns stay out of the cache until the key covers context.
    if has_history:
        return False
    return pii_count == 0
