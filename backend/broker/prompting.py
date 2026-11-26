"""Turn validated response constraints into instructions the model can follow.

Today a requested shape is only checked after generation, so the model is never
told about it: it answers freely, fails validation, and burns a cascade step.
These directives close that gap. They are a best effort — ``validate_response``
remains the authority on whether the answer actually complies.

Every value interpolated here has already passed ``ResponseConstraints``
validation, so field names and labels are bounded, pattern-checked strings.
"""

from backend.broker.validators import MAX_RESPONSE_CHARS, ResponseConstraints

DIRECTIVE_LIMIT = 1200


def constraint_directives(constraints: ResponseConstraints) -> str:
    """Instructions for the requested answer shape; empty when nothing is asked."""
    lines: list[str] = []
    if constraints.requires_json:
        fields = ", ".join(constraints.required_fields)
        if fields:
            lines.append(
                f"Reply with a single JSON object containing exactly these keys: {fields}."
            )
        else:
            lines.append("Reply with a single valid JSON object and no other text.")
        lines.append("Output only the JSON, with no prose before or after it.")
    if constraints.allowed_labels:
        labels = ", ".join(constraints.allowed_labels)
        lines.append(f"Answer with exactly one of these labels and nothing else: {labels}.")
    if constraints.min_bullets is not None or constraints.max_bullets is not None:
        low = constraints.min_bullets
        high = constraints.max_bullets
        if low is not None and high is not None:
            want = f"between {low} and {high}" if low != high else f"exactly {high}"
        elif high is not None:
            want = f"at most {high}"
        else:
            want = f"at least {low}"
        lines.append(f"Format the answer as {want} bullet points, one per line, starting '- '.")
    if constraints.max_chars < MAX_RESPONSE_CHARS:
        lines.append(f"Keep the answer under {constraints.max_chars} characters.")
    if constraints.min_chars > 1:
        lines.append(f"Write at least {constraints.min_chars} characters.")
    if not lines:
        return ""
    block = "\n".join(f"- {line}" for line in lines)
    # Bounded so a pathological constraint set cannot crowd out the conversation.
    return f"Answer requirements:\n{block}"[:DIRECTIVE_LIMIT]
