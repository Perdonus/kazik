from __future__ import annotations

import sqlite3
from pathlib import Path
import os
from typing import Any, Iterable, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "kazino.db"
DB_PATH = Path(os.environ.get("KAZINO_DB_PATH", DEFAULT_DB_PATH))

if os.environ.get("VERCEL") and "KAZINO_DB_PATH" not in os.environ:
    DB_PATH = Path("/tmp/kazino.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nickname TEXT UNIQUE NOT NULL,
  token TEXT UNIQUE,
  balance INTEGER NOT NULL DEFAULT 0,
  last_claim INTEGER NOT NULL DEFAULT 0,
  cases_opened INTEGER NOT NULL DEFAULT 0,
  cases_won INTEGER NOT NULL DEFAULT 0,
  upgrades INTEGER NOT NULL DEFAULT 0,
  upgrade_wins INTEGER NOT NULL DEFAULT 0,
  max_balance INTEGER NOT NULL DEFAULT 0,
  best_drop_item_id TEXT,
  best_upgrade_item_id TEXT,
  daily_cases INTEGER NOT NULL DEFAULT 0,
  daily_reset INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  rarity TEXT NOT NULL,
  price INTEGER NOT NULL,
  stattrak INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  case_id TEXT,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS giveaway_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  giveaway_id TEXT NOT NULL,
  entry INTEGER NOT NULL,
  joined_at INTEGER NOT NULL,
  UNIQUE(user_id, giveaway_id)
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def fetchone(query: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    with connect() as conn:
        cur = conn.execute(query, params)
        return cur.fetchone()


def fetchall(query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall()


def execute(query: str, params: Iterable[Any] = ()) -> None:
    with connect() as conn:
        conn.execute(query, params)
        conn.commit()


def execute_returning_id(query: str, params: Iterable[Any] = ()) -> int:
    with connect() as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return cur.lastrowid
