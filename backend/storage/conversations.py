"""Opt-in saved chat history.

This is the only place raw prompts and answers reach disk. Every write is gated
on the owner's ``workload_persistence_enabled`` preference, nothing else reads
these tables, and clearing the preference deletes every row.
"""

from time import time
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictStr

from backend.contracts import ContractModel
from backend.storage.connection import LocalDatabase

CONVERSATION_LIMIT = 200
MESSAGE_LIMIT = 500
TITLE_LIMIT = 80
CONTENT_LIMIT = 64000


class SavedMessage(ContractModel):
    role: Literal["user", "assistant"]
    content: StrictStr = Field(min_length=1, max_length=CONTENT_LIMIT, repr=False)
    model_id: StrictStr | None = None


class SavedConversation(ContractModel):
    id: UUID
    title: StrictStr = Field(min_length=1, max_length=TITLE_LIMIT, repr=False)
    created_at: float = Field(ge=0, strict=True)
    updated_at: float = Field(ge=0, strict=True)
    message_count: int = Field(default=0, ge=0, strict=True)


def derive_title(text: str) -> str:
    """First line of the opening message, bounded; never empty."""
    head = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not head:
        return "New conversation"
    return head[: TITLE_LIMIT - 1] + "…" if len(head) > TITLE_LIMIT else head


class ConversationStore:
    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def save(self, conversation_id: UUID, messages: tuple[SavedMessage, ...]) -> None:
        """Replace a conversation's messages, oldest conversations pruned first."""
        if not messages:
            raise ValueError("A saved conversation needs at least one message")
        if len(messages) > MESSAGE_LIMIT:
            raise ValueError("Conversation exceeds the stored message limit")
        for message in messages:
            SavedMessage.model_validate(message.model_dump(warnings=False))
        now = time()
        title = derive_title(messages[0].content)
        identity = str(conversation_id)
        with self._database.write() as connection:
            existing = connection.execute(
                "SELECT created_at FROM conversations WHERE id = ?", (identity,)
            ).fetchone()
            created = float(existing[0]) if existing else now
            connection.execute(
                "INSERT OR REPLACE INTO conversations (id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (identity, title, created, now),
            )
            connection.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?", (identity,)
            )
            connection.executemany(
                "INSERT INTO conversation_messages ("
                "conversation_id, position, role, content, model_id, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (identity, index, item.role, item.content, item.model_id, now)
                    for index, item in enumerate(messages)
                ],
            )
            connection.execute(
                "DELETE FROM conversations WHERE id NOT IN ("
                "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT ?)",
                (CONVERSATION_LIMIT,),
            )

    def list(self) -> tuple[SavedConversation, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.position) "
                "FROM conversations c "
                "LEFT JOIN conversation_messages m ON m.conversation_id = c.id "
                "GROUP BY c.id ORDER BY c.updated_at DESC"
            ).fetchall()
        return tuple(
            SavedConversation(
                id=UUID(str(row[0])),
                title=str(row[1]),
                created_at=float(row[2]),
                updated_at=float(row[3]),
                message_count=int(row[4]),
            )
            for row in rows
        )

    def messages(self, conversation_id: UUID) -> tuple[SavedMessage, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT role, content, model_id FROM conversation_messages "
                "WHERE conversation_id = ? ORDER BY position",
                (str(conversation_id),),
            ).fetchall()
        return tuple(
            SavedMessage(
                role="user" if str(row[0]) == "user" else "assistant",
                content=str(row[1]),
                model_id=str(row[2]) if row[2] is not None else None,
            )
            for row in rows
        )

    def delete(self, conversation_id: UUID) -> int:
        with self._database.write() as connection:
            # Messages are removed explicitly rather than relying on cascade, so
            # a connection without foreign keys still leaves no orphaned text.
            connection.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (str(conversation_id),),
            )
            cursor = connection.execute(
                "DELETE FROM conversations WHERE id = ?", (str(conversation_id),)
            )
            return int(cursor.rowcount or 0)

    def delete_all(self) -> int:
        with self._database.write() as connection:
            connection.execute("DELETE FROM conversation_messages")
            cursor = connection.execute("DELETE FROM conversations")
            return int(cursor.rowcount or 0)

    def count(self) -> int:
        with self._database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM conversations").fetchone()
        return int(row[0]) if row else 0
