"""Ingame-statistics ingestion from the StatsWebUI mod on the game VM.

The mod exposes the aggregated vanilla stats via HTTP on the WireGuard
tunnel (192.168.10.6:25580). This module pulls periodically and archives
everything cumulatively into SQLite on the RS, so the statistics views
survive VM pauses/outages and gain history the mod itself does not keep.

Vanilla stats are cumulative counters; UPSERT keeps the archive a
faithful mirror without any delta logic.
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Optional

import httpx

import db as dbm

log = logging.getLogger("ff.ingest")

_DEFAULT_SOURCE = "http://192.168.10.6:25580"


def _source_base() -> str:
    return os.environ.get("FF_STATS_INGEST_BASE", _DEFAULT_SOURCE).rstrip("/")


def _interval() -> float:
    try:
        return max(60.0, float(os.environ.get("FF_STATS_INGEST_SEC", "600")))
    except ValueError:
        return 600.0


_ingest_task: Optional[asyncio.Task] = None
_shared: Optional[httpx.AsyncClient] = None


ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingested_players (
  uuid           TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  play_hours     REAL NOT NULL DEFAULT 0,
  advancements_done INTEGER NOT NULL DEFAULT 0,
  advancements_total INTEGER NOT NULL DEFAULT 0,
  raw            TEXT NOT NULL,          -- compact payload (JSON)
  detail         TEXT,                   -- full /api/players/<uuid> payload
  fetched_at     REAL NOT NULL
);
"""


def _ensure_tables(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ingested_players)").fetchall()}
    if "ingested_players" in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
        if "detail" not in cols:
            conn.execute("ALTER TABLE ingested_players ADD COLUMN detail TEXT")
            conn.commit()
    conn.executescript(ARCHIVE_SCHEMA)
    conn.commit()


async def _client() -> httpx.AsyncClient:
    global _shared
    if _shared is None or _shared.is_closed:
        _shared = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0),
            headers={"User-Agent": "FriendlyFire-Ingest/1.0"},
        )
    return _shared


async def close_client() -> None:
    global _shared
    if _shared and not _shared.is_closed:
        await _shared.aclose()
        _shared = None


# ---------------------------------------------------------------------------
# Archive queries (used by the API layer)
# ---------------------------------------------------------------------------
def archived_players(limit: int = 200) -> list[dict]:
    conn = dbm.connect()
    try:
        tx = dbm.Database(conn)
        rows = tx.all_rows(
            "SELECT uuid,name,play_hours,advancements_done,advancements_total,"
            "raw,detail,fetched_at FROM ingested_players ORDER BY play_hours DESC"
            " LIMIT ?", max(1, min(int(limit), 500)))
        out = []
        for r in rows:
            try:
                raw = r["raw"] and json.loads(r["raw"])
            except Exception:
                raw = {}
            try:
                detail = (r["detail"] and json.loads(r["detail"])) or None
            except Exception:
                detail = None
            out.append({
                "uuid": r["uuid"], "name": r["name"],
                "playHours": r["play_hours"],
                "advancementsDone": r["advancements_done"],
                "advancementsTotal": r["advancements_total"],
                "fetchedAt": r["fetched_at"],
                "raw": raw, "detail": detail,
            })
        return out
    finally:
        conn.close()


def archive_health() -> dict:
    conn = dbm.connect()
    try:
        tx = dbm.Database(conn)
        n = tx.one("SELECT COUNT(*) c FROM ingested_players")
        freshest = tx.one("SELECT MAX(fetched_at) m FROM ingested_players")
        return {"archivedPlayers": int(n["c"]) if n else 0,
                "lastFetch": float(freshest["m"]) if freshest and freshest["m"] else None}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pull + upsert
# ---------------------------------------------------------------------------
async def ingest_once() -> dict:
    """Single pull cycle. Returns a small report for logging/tests."""
    cli = await _client()
    base = _source_base()
    report: dict[str, Any] = {"playersUpserted": 0, "detailsOk": 0, "ok": False}
    try:
        resp = await cli.get(f"{base}/api/players")
        resp.raise_for_status()
        payload = resp.json()
        players = payload.get("players", [])

        conn = dbm.connect()
        try:
            _ensure_tables(conn)
            tx = dbm.Database(conn)
            now = time.time()
            for p in players:
                adv = p.get("advancements") or {}

                # Full detail (54 boards + vanilla raw) per player.
                detail_json = None
                uuid = p.get("uuid", "")
                try:
                    det = await cli.get(f"{base}/api/players/{uuid}")
                    if det.status_code == 200:
                        dj = det.json()
                        detail_json = json.dumps(dj)
                        report["detailsOk"] += 1
                except Exception:
                    log.debug("detail fetch failed for %s", uuid)

                tx.run(
                    "INSERT INTO ingested_players"
                    " (uuid,name,play_hours,advancements_done,"
                    " advancements_total,raw,detail,fetched_at)"
                    " VALUES(?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(uuid) DO UPDATE SET"
                    " name=excluded.name, play_hours=excluded.play_hours,"
                    " advancements_done=excluded.advancements_done,"
                    " advancements_total=excluded.advancements_total,"
                    " raw=excluded.raw,"
                    " detail=COALESCE(excluded.detail, ingested_players.detail),"
                    " fetched_at=excluded.fetched_at",
                    uuid, p.get("name", ""),
                    float(p.get("playHours") or 0),
                    int(adv.get("done") or 0),
                    int(adv.get("total") or 0),
                    json.dumps(p), detail_json, now,
                )
                report["playersUpserted"] += 1
            tx.commit()
        finally:
            conn.close()
        report["ok"] = True
        return report
    except Exception as exc:
        log.warning("stats ingest failed: %s: %s",
                    type(exc).__name__, str(exc)[:120])
        report["error"] = f"{type(exc).__name__}"
        return report


async def _ingest_loop() -> None:
    log.info("stats ingest loop started (base=%s interval=%ss)",
             _source_base(), _interval())
    while True:
        try:
            rep = await ingest_once()
            if rep.get("ok"):
                log.info("stats ingest ok: %s players", rep["playersUpserted"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stats ingest iteration crashed")
        await asyncio.sleep(_interval())


def start_ingest() -> None:
    global _ingest_task
    if _ingest_task is None or _ingest_task.done():
        _ingest_task = asyncio.ensure_future(_ingest_loop())


def stop_ingest() -> None:
    global _ingest_task
    if _ingest_task is not None:
        _ingest_task.cancel()
        _ingest_task = None
