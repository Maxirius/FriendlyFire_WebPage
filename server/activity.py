"""Activity sampling + community statistics aggregation.

A lightweight collector samples the Minecraft status probe every five
minutes into SQLite (activity_samples) so the welcome page can draw a
24-hour activity graph without hammering the game servers. Retention:
two weeks (cleanup piggybacks on each sample run).
"""
import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Any, Optional

import mcstatus_service as mcstatus
import db as dbm

log = logging.getLogger("ff.stats")

INTERVAL = dbm.SAMPLE_INTERVAL_S
RETENTION = dbm.SAMPLE_RETENTION_S

_sampler_task: Optional[asyncio.Task] = None


def _bucket(ts: float) -> int:
    return int(ts // INTERVAL * INTERVAL)


async def _take_sample() -> None:
    data = await mcstatus.collect(force=True)
    by_id = {s["id"]: s for s in data["servers"]}
    surv = by_id.get("survival", {})
    crea = by_id.get("creative", {})
    net = by_id.get("network", {})
    conn = dbm.connect()
    try:
        tx = dbm.Database(conn)
        tx.run(
            "INSERT OR REPLACE INTO activity_samples"
            " (ts,players_survival,players_creative,players_network)"
            " VALUES(?,?,?,?)",
            _bucket(time.time()),
            int(surv.get("playersOnline") or 0),
            int(crea.get("playersOnline") or 0),
            int(net.get("playersOnline") or 0),
        )
        tx.run("DELETE FROM activity_samples WHERE ts < ?", time.time() - RETENTION)
        tx.commit()
    finally:
        conn.close()


async def _sampler_loop() -> None:
    log.info("activity sampler started (interval=%ss retention=%sd)",
             INTERVAL, RETENTION // 86400)
    while True:
        try:
            await _take_sample()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("activity sample failed")
        await asyncio.sleep(INTERVAL)


def start_sampler() -> None:
    global _sampler_task
    if _sampler_task is None or _sampler_task.done():
        _sampler_task = asyncio.ensure_future(_sampler_loop())


def stop_sampler() -> None:
    global _sampler_task
    if _sampler_task is not None:
        _sampler_task.cancel()
        _sampler_task = None


# ---------------------------------------------------------------------------
# Aggregations for the statistics section
# ---------------------------------------------------------------------------
async def activity_series(hours: int = 24) -> dict:
    """Downsampled series for the chart + peak/average figures."""
    cutoff = time.time() - hours * 3600
    conn = dbm.connect()
    try:
        tx = dbm.Database(conn)
        rows = tx.all_rows(
            "SELECT ts,players_survival,players_creative,players_network"
            " FROM activity_samples WHERE ts>=? ORDER BY ts", cutoff)
    finally:
        conn.close()

    # Bucket to hourly averages for a tidy 24-point chart.
    hour_buckets: dict[int, list[tuple[float,float,float,float]]] = defaultdict(list)
    for r in rows:
        hour_buckets[int(r["ts"] // 3600)].append(
            (float(r["ts"]),
             int(r["players_survival"]),
             int(r["players_creative"]),
             int(r["players_network"])))

    series = []
    peaks: dict[str, float] = {"survival": 0, "creative": 0, "network": 0}
    sums: dict[str, float] = {"survival": 0, "creative": 0, "network": 0}
    cnt = 0
    for hb in sorted(hour_buckets):
        pts = hour_buckets[hb]
        avg_s = sum(p[1] for p in pts) / len(pts)
        avg_c = sum(p[2] for p in pts) / len(pts)
        avg_n = sum(p[3] for p in pts) / len(pts)
        series.append({
            "ts": hb * 3600,
            "survival": round(avg_s, 1),
            "creative": round(avg_c, 1),
            "network": round(avg_n, 1),
        })
        for p in pts:
            peaks["survival"] = max(peaks["survival"], p[1])
            peaks["creative"] = max(peaks["creative"], p[2])
            peaks["network"] = max(peaks["network"], p[3])
            sums["survival"] += p[1]; sums["creative"] += p[2]; sums["network"] += p[3]
            cnt += 1

    avg = {k: (round(sums[k]/cnt, 2) if cnt else 0) for k in sums}
    return {
        "series": series,
        "peaks": peaks,
        "averages": avg,
        "coverageSamples": cnt,
        "historyHours": hours,
    }


def community_counts(tx: dbm.Database) -> dict:
    """Instant community figures from the auth DB (cheap aggregates)."""
    def scalar(sql: str, *params: Any) -> int:
        row = tx.one(sql, *params)
        return int(row["c"]) if row is not None else 0

    return {
        "membersActive": scalar(
            "SELECT COUNT(*) c FROM users WHERE disabled=0"),
        "whitelistedPlayers": scalar(
            "SELECT COUNT(DISTINCT uuid) c FROM ("
            " SELECT nickname_uuid uuid FROM users WHERE disabled=0"
            " AND nickname_uuid IS NOT NULL UNION ALL"
            " SELECT uuid FROM whitelist_extras)"),
        "pendingInvites": scalar(
            "SELECT COUNT(*) c FROM invites WHERE redeemed_at IS NULL"
            " AND expires_at>?", dbm.now()),
        "activeSessions": scalar(
            "SELECT COUNT(*) c FROM sessions WHERE revoked=0 AND expires_at>?",
            dbm.now()),
        "joinedLastWeek": scalar(
            "SELECT COUNT(*) c FROM users WHERE created_at>? AND disabled=0",
            dbm.now() - 7 * 86400),
    }
