"""Shared destination validation; loopback names normalize to numeric loopback."""

import re
from urllib.parse import urlsplit, urlunsplit


def validate_endpoint(endpoint: str, *, local: bool) -> str:
    """Reject ambiguous destinations and avoid localhost DNS at generation time."""
    try:
        parsed = urlsplit(endpoint)
        if (
            not endpoint
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in endpoint)
            or "\\" in endpoint
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or parsed.port == 0
            or any(segment in {".", ".."} for segment in parsed.path.split("/"))
        ):
            raise ValueError
        if local:
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
                or parsed.path not in {"", "/"}
            ):
                raise ValueError
            if parsed.hostname == "localhost":
                authority = "127.0.0.1" + (f":{parsed.port}" if parsed.port is not None else "")
                return urlunsplit((parsed.scheme, authority, "", "", ""))
        elif parsed.scheme != "https" or not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*",
            parsed.hostname,
        ):
            raise ValueError
        return endpoint.rstrip("/") if local else endpoint
    except (ValueError, TypeError):
        raise ValueError("Invalid configured provider destination") from None
