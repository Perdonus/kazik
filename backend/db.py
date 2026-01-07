from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parents[1]

# Env vars:
# - MONGODB_URI (required)
# - MONGODB_DB (optional, default: kazino)
_MONGODB_URI = os.environ.get("MONGODB_URI") or os.environ.get("KAZINO_MONGODB_URI")
_MONGODB_DB = os.environ.get("MONGODB_DB") or os.environ.get("KAZINO_MONGODB_DB") or "kazino"

_client: Optional[MongoClient] = None


def get_client() -> MongoClient:
    """Return a cached MongoClient.

    In serverless (Vercel) this dramatically reduces connection churn on warm invocations.
    """

    global _client
    if _client is None:
        if not _MONGODB_URI:
            raise RuntimeError(
                "MONGODB_URI is not set. Add it in your environment variables (Vercel Project Settings)."
            )
        _client = MongoClient(
            _MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
        )
    return _client


def get_db():
    return get_client()[_MONGODB_DB]


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
