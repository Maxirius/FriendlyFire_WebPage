"""Live Minecraft server status probing for the welcome page.

Pure-stdlib implementation of the Minecraft status ping (handshake +
status request) so the auth container needs no extra dependencies.
Targets sit behind the AMP wrapper on the Debian VM and are reached via
the WireGuard tunnel (198.18.0.6), never via published ports.

Known trap encoded here: AMP binds the game port itself while the app is
STOPPED and answers pings with a synthetic "Powered by AMP" MOTD. Such
answers are classified as ``stopped``, not ``online``.
"""
import asyncio
import json
import os
import re
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

# 1.8-era protocol constant; status responses work for any protocol.
_PROTO_VER = 47


def _env_targets() -> List[Dict[str, str]]:
    """Target list, overridable via FF_MC_STATUS_TARGETS (JSON array).

    ``kind`` distinguishes playable worlds ("world") from the proxy/
    network entry ("network") — the frontend lays them out differently.
    """
    default = [
        {"id": "survival", "label": "Survival", "kind": "world",
         "address": "198.18.0.6:25573"},
        {"id": "creative", "label": "Creative", "kind": "world",
         "address": "198.18.0.6:25574"},
        # The network entry is probed through the PRIMARY player door
        # (home WAN via DDNS + OPNsense NAT), matching what real clients
        # experience — NOT through the RS relay tunnel.
        {"id": "network", "label": "Netzwerk", "kind": "network",
         "address": "play.example.com:25565"},
    ]
    raw = os.environ.get("FF_MC_STATUS_TARGETS", "").strip()
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
        cleaned = []
        for t in parsed:
            cleaned.append({
                "id": str(t["id"]),
                "label": str(t.get("label") or t["id"]).capitalize(),
                "kind": str(t.get("kind") or "world"),
                "address": str(t["address"]),
            })
        return cleaned or default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Wire format helpers
# ---------------------------------------------------------------------------
def _pack_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    """Return (value, next_pos); ValueError on overrun."""
    result = shift = 0
    for _ in range(5):          # a varint is at most 5 bytes
        if pos >= len(buf):
            raise ValueError("varint overruns buffer")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
    raise ValueError("varint too long")


def _pack_str(s: str) -> bytes:
    data = s.encode("utf-8")
    return _pack_varint(len(data)) + data


def parse_status_frame(buf: bytes) -> Optional[Tuple[dict, int]]:
    """Try to decode a complete status response from accumulated bytes.

    Returns (json_dict, consumed_length) or None while incomplete /
    malformed-prefix. Garbage prefixes (implausible frame lengths) are
    skipped byte-wise until a plausible frame header appears.
    """
    pos = 0
    while True:
        try:
            total_len, nxt = _read_varint(buf, pos)
        except ValueError:
            return None
        if total_len <= 0 or total_len > 1 << 22:   # implausible frame ->
            return None                             # treat whole buf as junk
        if len(buf) < nxt + total_len:
            return None
        frame = buf[nxt:nxt + total_len]
        pkt_id, pos2 = _read_varint(frame, 0)
        if pkt_id != 0:
            pos = nxt + total_len                   # not a status packet
            continue
        slen, pos3 = _read_varint(frame, pos2)
        blob = frame[pos3:pos3 + slen]
        if len(blob) < slen:
            raise ValueError("string overruns frame")
        try:
            return json.loads(blob.decode("utf-8")), nxt + total_len
        except (UnicodeDecodeError, json.JSONDecodeError):
            pos = nxt + total_len
            continue


_FORMAT_CODE = re.compile("\u00a7.")
_AMP_FAKE_MOTD = re.compile(r"powered\s+by\s+amp", re.IGNORECASE)


def clean_motd(description: Any) -> str:
    """Flatten a status-description (str or component tree) to plain text."""
    def flatten(node: Any) -> str:
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            parts = [flatten(node.get(k, "")) for k in ("text",)]
            parts.extend(flatten(extra) for extra in node.get("extra", []))
            return "".join(parts)
        if isinstance(node, list):
            return "".join(flatten(item) for item in node)
        return ""
    return _FORMAT_CODE.sub("", flatten(description)).strip()


def classify(js: dict) -> Tuple[str, str]:
    """-> (state, motd_clean). state in online|stopped."""
    motd = clean_motd(js.get("description", ""))
    if _AMP_FAKE_MOTD.search(motd.replace("\u00a7r", "")):
        return "stopped", motd
    return "online", motd


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------
async def probe(address: str, timeout: float = 3.0) -> Tuple[dict, int]:
    """Raw status ping. Returns ({response-json-or-empty}, latency_ms).

    Raises OSError/timeouts for unreachable targets; callers decide the
    resulting presentation state.
    """
    host, sep, port_s = address.rpartition(":")
    if not sep:
        host, port_s = address, "25565"
    port = int(port_s)

    opened = time.monotonic()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout)
    try:
        handshake = (_pack_varint(_PROTO_VER) + _pack_str(host)
                     + struct.pack(">H", port) + _pack_varint(1))
        pkt = _pack_varint(0x00) + handshake
        writer.write(_pack_varint(len(pkt)) + pkt)
        req = _pack_varint(0x00)
        writer.write(_pack_varint(len(req)) + req)
        await asyncio.wait_for(writer.drain(), timeout)

        buf = b""
        deadline = opened + timeout * 2
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("status response incomplete")
            chunk = await asyncio.wait_for(reader.read(4096),
                                           min(remaining, timeout))
            if not chunk:
                break
            buf += chunk
            try:
                decoded = parse_status_frame(buf)
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                decoded = None
            if decoded is not None:
                js, _consumed = decoded
                lat = int((time.monotonic() - opened) * 1000)
                return js, lat
        raise TimeoutError("connection closed before status arrived")
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except Exception:
            pass
        # Belt & braces: guarantee the underlying transport is gone even
        # when the peer half-closes oddly (AMP wrapper behaviour).
        transport = getattr(writer, "transport", None)
        if transport is not None:
            transport.abort()


# ---------------------------------------------------------------------------
# Naked TCP transit (no protocol overhead)
# ---------------------------------------------------------------------------
_TUNNEL_TRANSIT_DEFAULT = "198.18.0.6:25565"


def _transit_target() -> str:
    return os.environ.get("FF_TUNNEL_TRANSIT_TARGET",
                          _TUNNEL_TRANSIT_DEFAULT).strip()


async def tcp_transit_ms(target: str, timeout: float = 3.0,
                         samples: int = 2) -> Optional[int]:
    """Round-trip of a bare TCP connect (SYN/SYN-ACK/ACK), best of N.

    Unlike the protocol ping this excludes JVM/proxy processing, so it
    approximates the pure network transit of a path — usable as the
    additive tunnel component in latency estimates.
    """
    host, sep, port_s = target.rpartition(":")
    if not sep:
        host, port_s = target, "25565"
    port = int(port_s)
    best: Optional[float] = None
    for _ in range(max(1, samples)):
        opened = time.monotonic()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout)
        except Exception:
            continue
        try:
            lat = (time.monotonic() - opened) * 1000
            best = lat if best is None else min(best, lat)
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1.0)
            except Exception:
                pass
            transport = getattr(writer, "transport", None)
            if transport is not None:
                transport.abort()
    return None if best is None else int(round(best))


# ---------------------------------------------------------------------------
# Aggregation with a small cache (frontend polls every 30 s)
# ---------------------------------------------------------------------------
_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}


def _cache_ttl() -> float:
    try:
        return max(5.0, float(os.environ.get("FF_MC_STATUS_TTL_S", "20")))
    except ValueError:
        return 20.0




def shape_entry(target: dict, js: Optional[dict],
                latency: Optional[int], error: Optional[str]) -> dict:
    if js is None:
        return {
            "id": target["id"], "label": target["label"],
            "kind": target.get("kind", "world"),
            "state": "offline", "playersOnline": None, "playersMax": None,
            "latencyMs": None, "version": None, "motd": None,
            "playerNames": [], "error": error or "unreachable",
        }
    state, motd = classify(js)
    players = js.get("players", {}) or {}
    sample = [p.get("name", "") for p in (players.get("sample") or [])
              if p.get("name")][:12]
    version = ((js.get("version", {}) or {}).get("name"))
    return {
        "id": target["id"], "label": target["label"],
        "kind": target.get("kind", "world"),
        "state": state,
        "playersOnline": players.get("online"),
        "playersMax": players.get("max"),
        "latencyMs": latency, "version": version, "motd": motd[:160],
        "playerNames": sample, "error": None,
    }


async def collect(force: bool = False) -> dict:
    """Probe all targets (parallel), cached for FF_MC_STATUS_TTL_S."""
    now = time.time()
    if not force and _CACHE["data"] is not None \
            and now - _CACHE["ts"] < _cache_ttl():
        return _CACHE["data"]

    targets = _env_targets()

    async def grab(t: dict) -> dict:
        try:
            js, lat = await probe(t["address"])
            return shape_entry(t, js, lat, None)
        except Exception as exc:
            return shape_entry(t, None, None, f"{type(exc).__name__}")

    entries = await asyncio.gather(*(grab(t) for t in targets))

    # Overlay the proxy-reported presence (precise: full names + server
    # association) onto the SLP picture. SLP samples are capped/ordered
    # arbitrarily; the plugin heartbeat is the authoritative list.
    try:
        import presence
        snap = presence.snapshot()
        if snap["fresh"]:
            all_ids = {p["name"] for p in snap["players"]}
            for entry in entries:
                if entry["kind"] == "network":
                    names = sorted(all_ids)
                    online = len(snap["players"])
                else:
                    subset = presence.by_server(entry["id"])
                    names = sorted(p["name"] for p in subset)
                    online = len(subset)
                entry["playerNames"] = names
                entry["playersOnline"] = online
                if online == 0 and entry["state"] == "online":
                    # empty but reachable: keep state, names stay empty
                    pass
    except Exception:
        pass  # presence overlay must never break the status feed

    transit = await tcp_transit_ms(_transit_target())
    data = {
        "checkedAt": int(now),
        "nextRefreshSec": int(_cache_ttl()),
        "tunnelTransitMs": transit,
        "servers": list(entries),
    }
    _CACHE["ts"] = now
    _CACHE["data"] = data
    return data
