"""Persistence layer: SQLite via stdlib, WAL mode, tiny DAO helpers."""
import os
import sqlite3
import threading
import time
from typing import Any, Optional

_DB_PATH = os.environ.get("FF_AUTH_DB", "/data/auth.db")

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_hash       TEXT NOT NULL,
  nickname      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  nickname_uuid TEXT,
  avatar        BLOB,
  role          TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
  invites_enabled INTEGER NOT NULL DEFAULT 0,
  invited_by    INTEGER REFERENCES users(id),
  disabled      INTEGER NOT NULL DEFAULT 0,
  created_at    REAL NOT NULL,
  last_login_at REAL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS invites (
  code_hash       TEXT PRIMARY KEY,
  code_plain      TEXT NOT NULL UNIQUE,
  created_by      INTEGER NOT NULL REFERENCES users(id),
  recipient_email TEXT COLLATE NOCASE,
  created_at      REAL NOT NULL,
  expires_at      REAL NOT NULL,
  redeemed_at     REAL,
  redeemed_by     INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_invites_creator ON invites(created_by);

CREATE TABLE IF NOT EXISTS resets (
  token_hash TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  used_at    REAL
);

CREATE TABLE IF NOT EXISTS whitelist_extras (
  uuid      TEXT PRIMARY KEY,          -- hex, no dashes
  name      TEXT NOT NULL,
  added_by  INTEGER REFERENCES users(id),
  source    TEXT NOT NULL DEFAULT 'manual',
  note      TEXT,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         REAL NOT NULL,
  actor_user INTEGER,
  event      TEXT NOT NULL,
  detail     TEXT,
  ip         TEXT
);

CREATE TABLE IF NOT EXISTS activity_samples (
  ts           REAL PRIMARY KEY,      -- epoch, bucket-aligned
  players_survival INTEGER NOT NULL,
  players_creative INTEGER NOT NULL,
  players_network  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_samples(ts);
"""

SAMPLE_INTERVAL_S = 300          # 5-minute buckets
SAMPLE_RETENTION_S = 14 * 86400  # two weeks of history


def now() -> float:
    """Injectable clock for tests."""
    return time.time()


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path if db_path is not None else _DB_PATH,
        timeout=10,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init(db_path: Optional[str] = None) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


class Database:
    """Request-scoped facade holding one connection."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- generic -----------------------------------------------------------
    def one(self, sql: str, *params: Any) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return row

    def all_rows(self, sql: str, *params: Any) -> list[sqlite3.Row]:
        cur = self.conn.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    def run(self, sql: str, *params: Any) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        return cur

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def immediate(self) -> None:
        """Acquire RESERVED lock: serializes invite-redemption races."""
        self.conn.execute("BEGIN IMMEDIATE")


# ---- meta helpers ----------------------------------------------------------
def meta_get(db: Database, key: str) -> Optional[str]:
    row = db.one("SELECT value FROM meta WHERE key=?", key)
    return row["value"] if row else None


def meta_set(db: Database, key: str, value: str) -> None:
    db.run(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        key, value,
    )


def audit(db: Database, event: str, actor: Optional[int],
          detail: str = "", ip: str = "") -> None:
    db.run(
        "INSERT INTO audit(ts,actor_user,event,detail,ip) VALUES(?,?,?,?,?)",
        now(), actor, event, detail[:400], ip[:64],
    )


REV_KEY = "whitelist_rev"


def bump_whitelist_revision(db: Database) -> int:
    """Monotonic revision for the whitelist sync consumers."""
    row = db.one(f"SELECT value FROM meta WHERE key='{REV_KEY}'")
    rev = (int(row["value"]) if row else 0) + 1
    meta_set(db, REV_KEY, str(rev))
    return rev


def whitelist_revision(db: Database) -> int:
    row = db.one(f"SELECT value FROM meta WHERE key='{REV_KEY}'")
    return int(row["value"]) if row else 0
