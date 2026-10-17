"""Live player presence, reported by the FfWhitelistSync Velocity plugin.

The proxy is the source of truth for who is online (SLP samples are
capped and unordered). The plugin pushes the full list on join/quit/
server-switch plus a 20 s heartbeat; this module holds the freshest
snapshot in memory. mcstatus_service merges it into /api/status/minecraft
when fresh — the frontend consumes the merged status unchanged.
"""
import threading
import time
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_players: List[Dict[str, Any]] = []
_updated_at: float = 0.0

STALE_S = 90.0


def update(players: List[Dict[str, Any]], ts: Optional[float] = None) -> None:
    """Replace the snapshot (called by the bearer-guarded sync route)."""
    global _players, _updated_at
    clean = []
    for p in players or []:
        name = str(p.get("name", "")).strip()
        uuid = str(p.get("uuid", "")).strip().lower()
        server = str(p.get("server", "")).strip().lower()
        if not name or not uuid:
            continue
        clean.append({
            "name": name[:16],
            "uuid": uuid,
            "server": server[:32],
            "since": float(p.get("since") or 0) or None,
        })
    with _lock:
        _players = clean
        _updated_at = ts if ts is not None else time.time()


def snapshot() -> Dict[str, Any]:
    """{'fresh': bool, 'players': [...], 'updatedAt': float}"""
    with _lock:
        return {
            "fresh": (time.time() - _updated_at) < STALE_S and bool(_updated_at),
            "players": list(_players),
            "updatedAt": _updated_at,
        }


def by_server(server_id: str) -> List[Dict[str, Any]]:
    snap = snapshot()
    if not snap["fresh"]:
        return []
    sid = server_id.lower()
    return [p for p in snap["players"] if p["server"] == sid]


def all_players() -> List[Dict[str, Any]]:
    snap = snapshot()
    return snap["players"] if snap["fresh"] else []
