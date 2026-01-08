from __future__ import annotations

import os
from typing import Optional

from pymongo import MongoClient

_client: Optional[MongoClient] = None


def _get_env_uri() -> str | None:
    # Приоритет: что создала Vercel-интеграция
    return (
        os.environ.get("STORAGE_MONGODB_URI")
        or os.environ.get("MONGODB_URI")
        or os.environ.get("KAZINO_MONGODB_URI")
        or os.environ.get("KAZINO_MONGODB_URI".lower())  # на всякий
    )


def _get_env_dbname() -> str:
    # Приоритет: что создала Vercel-интеграция
    return (
        os.environ.get("STORAGE_MONGODB_DB")
        or os.environ.get("MONGODB_DB")
        or os.environ.get("KAZINO_MONGODB_DB")
        or "kazino"
    )


def get_client() -> MongoClient:
    """Return cached MongoClient.

    В serverless это снижает churn подключений на warm-invocations.
    """

    global _client
    if _client is None:
        uri = _get_env_uri()
        if not uri:
            raise RuntimeError(
                "MongoDB URI not set. Set STORAGE_MONGODB_URI (Vercel integration) "
                "or MONGODB_URI in Vercel Environment Variables."
            )

        # Важно: MongoClient сам по себе ленивый — реальное подключение при первом запросе.
        _client = MongoClient(
            uri,
            # таймауты чтобы не висело вечно, но и не было слишком агрессивно
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000,
            socketTimeoutMS=15000,
        )

    return _client


def get_db():
    return get_client()[_get_env_dbname()]


def users_col():
    return get_db()["users"]


def items_col():
    return get_db()["items"]


def giveaway_entries_col():
    return get_db()["giveaway_entries"]


def init_db() -> None:
    """Create indexes (idempotent)."""

    users = users_col()
    items = items_col()
    entries = giveaway_entries_col()

    # Users
    users.create_index("nickname", unique=True)
    users.create_index("token", unique=True, sparse=True)

    # Items
    items.create_index("user_id")
    items.create_index([("user_id", 1), ("status", 1), ("created_at", -1)])

    # Giveaways
    entries.create_index([("user_id", 1), ("giveaway_id", 1)], unique=True)
    entries.create_index([("user_id", 1), ("joined_at", -1)])
