"""Private, nonserializable placeholder state owned by one request."""

import re
from typing import NoReturn, SupportsIndex
from uuid import UUID, uuid4

from backend.privacy.analyzer import PrivacyError

RESERVED_PLACEHOLDER = re.compile(r"\[\[PII_[^\]\r\n]{0,256}\]\]|\[[A-Z][A-Z_]{0,63}_[0-9]+\]")
_TOKEN = re.compile(r"\[\[PII_[a-f0-9]{32}_[A-Z][A-Z_]{0,63}_[1-9][0-9]*\]\]")


class PlaceholderMap:
    __slots__ = ("_closed", "_counter", "_namespace", "_request_id", "_reverse", "_values")

    def __init__(self, request_id: UUID) -> None:
        self._request_id = request_id
        self._namespace = uuid4().hex
        self._values: dict[str, str] = {}
        self._reverse: dict[tuple[str, str], str] = {}
        self._counter = 0
        self._closed = False

    @property
    def request_id(self) -> UUID:
        return self._request_id

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(self._values)

    @property
    def entry_count(self) -> int:
        return len(self._values)

    def add(self, entity_type: str, value: str) -> str:
        if self._closed or not re.fullmatch(r"[A-Z][A-Z_]{0,63}", entity_type) or not value:
            raise PrivacyError("Placeholder map cannot accept this entry")
        key = (entity_type, value)
        if key not in self._reverse:
            self._counter += 1
            token = f"[[PII_{self._namespace}_{entity_type}_{self._counter}]]"
            self._reverse[key] = token
            self._values[token] = value
        return self._reverse[key]

    def _validate(self, request_id: UUID, text: str) -> None:
        if request_id != self._request_id or self._closed:
            raise PrivacyError("Placeholder map is unavailable for this request")
        residual = _TOKEN.sub(
            lambda match: "" if match.group() in self._values else match.group(), text
        )
        if RESERVED_PLACEHOLDER.search(residual) or "[[PII_" in residual:
            raise PrivacyError("Output contains unknown placeholder tokens")

    def text_for_scan(self, request_id: UUID, text: str) -> str:
        self._validate(request_id, text)
        return _TOKEN.sub(lambda match: " " * len(match.group()), text)

    def restore(self, request_id: UUID, text: str) -> tuple[str, bool]:
        self._validate(request_id, text)
        found = bool(_TOKEN.search(text))
        return _TOKEN.sub(lambda match: self._values[match.group()], text), found

    def clear(self) -> None:
        self._values.clear()
        self._reverse.clear()
        self._closed = True

    def __repr__(self) -> str:
        return "PlaceholderMap(<redacted>)"

    def __getstate__(self) -> NoReturn:
        raise TypeError("placeholder map serialization is forbidden")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("placeholder map serialization is forbidden")

    def model_dump(self) -> NoReturn:
        raise TypeError("placeholder map serialization is forbidden")

    def model_dump_json(self) -> NoReturn:
        raise TypeError("placeholder map serialization is forbidden")
