"""Owner-added cloud destinations.

Endpoints arrive from the local UI, so each is validated before a row is written
and again when it is read back: a stored row can never widen where sanitized
text may travel beyond what the shared endpoint contract allows. API keys are
never persisted here. Provider construction stays in the orchestrator, so this
module deliberately holds no provider import.
"""

from ipaddress import ip_address
from time import time
from urllib.parse import urlsplit

from backend.config.endpoints import validate_endpoint
from backend.config.loader import CloudDestinationConfig
from backend.storage.connection import LocalDatabase

DESTINATION_LIMIT = 20


def _reject_address_literals(endpoint: str) -> None:
    """Require a DNS name, not a bare IP.

    ``validate_endpoint`` accepts any hostname shape, and an address literal
    satisfies it. That was harmless while destinations came only from
    owner-edited configuration, but these arrive from the UI, where a literal
    would let the cloud path reach loopback, a private range or a link-local
    metadata service. Real providers are always reached by name.
    """
    host = urlsplit(endpoint).hostname or ""
    try:
        ip_address(host)
    except ValueError:
        return
    raise ValueError("Cloud destinations must use a DNS hostname")


def _validated(fields: dict[str, object]) -> CloudDestinationConfig:
    """Field shapes plus the same https/host rules the packaged entries get."""
    config = CloudDestinationConfig.model_validate(fields)
    for endpoint in (config.endpoint, config.health_endpoint):
        if endpoint is None:
            continue
        validate_endpoint(endpoint, local=False)
        _reject_address_literals(endpoint)
    return config


class CloudDestinationStore:
    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def add(self, destination: CloudDestinationConfig) -> None:
        validated = _validated(destination.model_dump())
        with self._database.write() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM owner_cloud_destinations WHERE model_id != ?",
                (validated.model_id,),
            ).fetchone()
            if int(row[0]) >= DESTINATION_LIMIT:
                raise ValueError("Too many owner cloud destinations")
            connection.execute(
                "INSERT OR REPLACE INTO owner_cloud_destinations ("
                "model_id, display_name, endpoint, health_endpoint, created_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    validated.model_id,
                    validated.display_name,
                    validated.endpoint,
                    validated.health_endpoint,
                    time(),
                ),
            )

    def list(self) -> tuple[CloudDestinationConfig, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT model_id, display_name, endpoint, health_endpoint "
                "FROM owner_cloud_destinations ORDER BY created_at"
            ).fetchall()
        destinations: list[CloudDestinationConfig] = []
        for row in rows:
            try:
                destinations.append(
                    _validated(
                        {
                            "model_id": str(row[0]),
                            "display_name": str(row[1]),
                            "endpoint": str(row[2]),
                            "health_endpoint": str(row[3]) if row[3] is not None else None,
                            "model_version": "owner-v1",
                        }
                    )
                )
            except ValueError:
                # A row that no longer satisfies the endpoint contract is skipped
                # rather than trusted, so tightening validation cannot be bypassed
                # by something written under older rules.
                continue
        return tuple(destinations)

    def remove(self, model_id: str) -> int:
        with self._database.write() as connection:
            cursor = connection.execute(
                "DELETE FROM owner_cloud_destinations WHERE model_id = ?", (model_id,)
            )
            return int(cursor.rowcount or 0)
