from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pymongo import MongoClient

# ⚠️ Compatibility: some older code may expect db.BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[1]

_DEFAULT_DB_NAME = "kazino"
_client: Optional[MongoClient] = None


def _get_env(*names: str) -> str | None:
    """Return first non-empty env var value; also strips accidental wrapping quotes."""
    for name in names:
        v = os.environ.get(name)
        if not v:
            continue
        v = v.strip()
        # strip accidental "..." or '...'
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1].strip()
        if v:
            return v
    return None


def get_mongodb_uri() -> str:
    uri = _get_env(
        "MONGODB_URI",            # official Atlas↔Vercel integration env var
        "STORAGE_MONGODB_URI",    # what you created in Vercel Storage
        "KAZINO_MONGODB_URI",     # fallback
    )
    if not uri:
        raise RuntimeError(
            "MongoDB URI is not set. Add env var MONGODB_URI (preferred) "
            "or STORAGE_MONGODB_URI in Vercel Project Settings."
        )
    return uri


def get_db_name() -> str:
    return _get_env("MONGODB_DB", "KAZINO_MONGODB_DB") or _DEFAULT_DB_NAME


def get_client() -> MongoClient:
    """Return cached MongoClient (important for Vercel serverless warm invocations)."""
    global _client
    if _client is None:
        _client = MongoClient(
            get_mongodb_uri(),
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
            maxPoolSize=int(_get_env("MONGODB_MAX_POOL_SIZE") or "10"),
        )
    return _client


def get_db():
    return get_client()[get_db_name()]


def users_col():
    return get_db()["users"]


def items_col():
    return get_db()["items"]


def giveaway_entries_col():
    return get_db()["giveaway_entries"]


def init_db() -> None:
    """Create indexes (idempotent). Safe to call on each cold start."""
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
