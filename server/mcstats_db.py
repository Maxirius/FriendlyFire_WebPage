"""Read access to the MCStats-to-SQL MariaDB (fed by the game-server mod).

The mod (MCStats-to-SQL on the survival server) mirrors vanilla stats
and live events into the `mcstats` database on the local MariaDB. This
module gives the auth service read-only views over that data:

- players / stats / advancements  — harvested mirrors
- events                          — pvp_kill, death, join, leave stream

Configuration via env (see auth.env.template):
    FF_MCSTATS_DB_HOST (default 127.0.0.1 — MariaDB container is exposed
                        on the docker host via socat on 10.252.1.1)
    FF_MCSTATS_DB_PORT (default 3306)
    FF_MCSTATS_DB_USER / FF_MCSTATS_DB_PASS
    FF_MCSTATS_DB_NAME (default mcstats)

Connections are opened per request (small site, tiny cost) and never
pool — the reader user has SELECT-only grants.
"""
import logging
import os
from typing import Any, Optional

import pymysql

log = logging.getLogger("ff.mcstats")


class DbMissing(RuntimeError):
    """Raised when the mod has not created its schema yet."""


_ENABLED: Optional[bool] = None


def _cfg() -> Optional[dict]:
    """Returns connection kwargs, or None when the integration is off."""
    global _ENABLED
    if _ENABLED is False:
        return None
    user = os.environ.get("FF_MCSTATS_DB_USER", "").strip()
    password = os.environ.get("FF_MCSTATS_DB_PASS", "").strip()
    if not user or not password:
        if _ENABLED is None:
            log.info("mcstats db integration disabled (no credentials)")
        _ENABLED = False
        return None
    _ENABLED = True
    return {
        "host": os.environ.get("FF_MCSTATS_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("FF_MCSTATS_DB_PORT", "3306")),
        "user": user,
        "password": password,
        "database": os.environ.get("FF_MCSTATS_DB_NAME", "mcstats"),
        "charset": "utf8mb4",
        "connect_timeout": 4,
        "read_timeout": 8,
        "cursorclass": pymysql.cursors.DictCursor,
    }


def _query(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    """Runs one SELECT; raises on connection trouble (caller decides)."""
    cfg = _cfg()
    if cfg is None:
        raise RuntimeError("mcstats db not configured")
    conn = pymysql.connect(**cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())
    except pymysql.err.ProgrammingError as exc:
        code = exc.args[0] if exc.args else 0
        if code == 1146:  # table missing -> mod hasn't initialised yet
            raise DbMissing(str(exc)) from exc
        raise
    finally:
        conn.close()


def available() -> bool:
    """True when credentials are configured (cheap check, no I/O)."""
    return _cfg() is not None


# ---------------------------------------------------------------------------
# Public read helpers
# ---------------------------------------------------------------------------

def player_by_name(name: str) -> Optional[dict]:
    row = _query(
        "SELECT uuid, name, first_seen, last_seen FROM players "
        "WHERE LOWER(name) = LOWER(%s) LIMIT 1", (name,))
    return row[0] if row else None


def player_stats(uuid: str) -> dict[str, dict]:
    """stat key -> {'value': n} for one player."""
    rows = _query(
        "SELECT stat, value FROM stats WHERE player_uuid = %s", (uuid,))
    return {r["stat"]: {"value": r["value"]} for r in rows}


def player_advancements(uuid: str) -> list[str]:
    rows = _query(
        "SELECT advancement FROM advancements WHERE player_uuid = %s "
        "ORDER BY advancement", (uuid,))
    return [r["advancement"] for r in rows]


def all_players() -> list[dict]:
    """Player roster with a few headline aggregates."""
    return _query(
        """
        SELECT p.uuid, p.name, p.first_seen, p.last_seen,
               SUM(CASE WHEN s.stat = 'custom:minecraft:play_time_ms'
                        THEN s.value ELSE 0 END)            AS play_ms,
               SUM(CASE WHEN s.stat = 'custom:minecraft:deaths'
                        THEN s.value ELSE 0 END)            AS deaths,
               (SELECT COUNT(*) FROM advancements a
                 WHERE a.player_uuid = p.uuid)             AS adv_done
        FROM players p
        LEFT JOIN stats s ON s.player_uuid = p.uuid
        GROUP BY p.uuid, p.name, p.first_seen, p.last_seen
        ORDER BY play_ms DESC
        """)


def events(player_uuid: Optional[str] = None, kind: Optional[str] = None,
           limit: int = 100) -> list[dict]:
    """Recent events, optionally filtered by player and/or kind."""
    limit = max(1, min(int(limit), 500))
    sql = ("SELECT ts, kind, player_uuid, opponent_uuid, detail "
           "FROM events")
    where: list[str] = []
    args: list[Any] = []
    if player_uuid:
        where.append("(player_uuid = %s OR opponent_uuid = %s)")
        args.extend([player_uuid, player_uuid])
    if kind:
        where.append("kind = %s")
        args.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT %s"
    args.append(limit)
    return _query(sql, tuple(args))


def pvp_summary(player_uuid: str) -> list[dict]:
    """Killer -> victim aggregation for one player (either side)."""
    return _query(
        """
        SELECT CASE WHEN player_uuid = %s THEN 'kills'
                    ELSE 'deaths_by' END      AS side,
               opponent_uuid                  AS other_uuid,
               COUNT(*)                       AS cnt
        FROM events
        WHERE kind = 'pvp_kill'
          AND (player_uuid = %s OR opponent_uuid = %s)
        GROUP BY side, other_uuid
        ORDER BY cnt DESC
        """, (player_uuid, player_uuid, player_uuid))
