"""Tests for the pure-stdlib Minecraft status probe (mcstatus_service)."""
import asyncio
import json

import pytest

import mcstatus_service as mcs


# ---------------------------------------------------------------------------
# Varint codec
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", [0, 1, 127, 128, 300, 16384, 2147483647])
def test_pack_varint_roundtrip(value):
    packed = mcs._pack_varint(value)
    got, nxt = mcs._read_varint(packed, 0)
    assert got == value
    assert nxt == len(packed)


def test_read_varint_rejects_overlong():
    with pytest.raises(ValueError):
        mcs._read_varint(b"\xff\xff\xff\xff\xff\x01", 0)


# ---------------------------------------------------------------------------
# Frame parsing
# ---------------------------------------------------------------------------
def _frame(payload_json: str) -> bytes:
    inner = b"\x00" + mcs._pack_str(payload_json)
    return mcs._pack_varint(len(inner)) + inner


def test_parse_status_frame_complete():
    js = {"players": {"online": 3, "max": 20},
          "version": {"name": "26.3"}}
    got = mcs.parse_status_frame(_frame(json.dumps(js)))
    assert got is not None
    decoded, consumed = got
    assert decoded["players"]["online"] == 3
    assert consumed == len(_frame(json.dumps(js)))


def test_parse_status_frame_fragmented_returns_none_until_complete():
    raw = _frame(json.dumps({"players": {"online": 0, "max": 20}}))
    half = len(raw) // 2
    assert mcs.parse_status_frame(raw[:half]) is None
    assert mcs.parse_status_frame(raw) is not None


def test_parse_status_frame_junk_yields_none_no_crash():
    raw = _frame("{}")
    # Leading junk makes the whole accumulation unusable -> None, no crash.
    assert mcs.parse_status_frame(b"\xff\xff\xff" + raw) is None
    # Clean buffer still parses normally afterwards.
    assert mcs.parse_status_frame(raw) is not None


# ---------------------------------------------------------------------------
# MOTD cleaning + classification
# ---------------------------------------------------------------------------
def test_clean_motd_component_tree_with_codes():
    desc = {"text": "", "extra": [
        {"text": "\u00a73FriendlyFire\u00a7r"},
        {"text": " \u00a78| "},
        {"text": "\u00a7eSurvival"},
    ]}
    assert mcs.clean_motd(desc) == "FriendlyFire | Survival"


def test_classify_online_vs_amp_fake():
    online_js = {"description": "\u00a73FriendlyFire\u00a7r \u00a78| "
                                  "\u00a7eSurvival"}
    stopped_js = {"description": "Powered by AMP"}
    assert mcs.classify(online_js)[0] == "online"
    assert mcs.classify(stopped_js)[0] == "stopped"


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------
def test_shape_entry_offline_has_null_fields():
    shaped = mcs.shape_entry({"id": "x", "label": "X"}, None, None,
                             "TimeoutError")
    assert shaped["state"] == "offline"
    assert shaped["playersOnline"] is None
    assert shaped["playerNames"] == []


def test_shape_entry_sample_names_cap():
    js = {"players": {"online": 2, "max": 20,
                      "sample": [{"name": "Alice"}, {"name": "Bob"}]},
          "version": {"name": "26.3"}}
    shaped = mcs.shape_entry({"id": "s", "label": "Survival"}, js, 12, None)
    assert shaped["playerNames"] == ["Alice", "Bob"]
    assert shaped["state"] == "online"


# ---------------------------------------------------------------------------
# Collect aggregation (against a live loopback listener speaking the
# protocol; ensures the wire code really speaks Minecraft)
# ---------------------------------------------------------------------------
class _FakeServer:
    """Emulates a minimal status responder on stream callbacks."""

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer

    async def speak(self) -> None:
        js = json.dumps({
            "description": {"text": "\u00a73FriendlyFire\u00a7r \u00a78| "
                                     "\u00a7eSurvival"},
            "players": {"online": 2, "max": 20,
                        "sample": [{"name": "Alice"}]},
            "version": {"name": "26.3"},
        })
        inner = b"\x00" + mcs._pack_str(js)
        self.writer.write(mcs._pack_varint(len(inner)) + inner)
        await self.writer.drain()
        # Deliberately NOT waiting for closure here: the client aborts
        # its transport after reading, and waiting for the FIN exchange
        # would deadlock server.wait_closed().
        self.writer.transport.abort()


async def _fake_handle(reader, writer) -> None:
    await _FakeServer(reader, writer).speak()


def test_probe_against_live_socket():
    """Runs via asyncio.run: anyio's threaded portal leaks stream
    transports at teardown (selector spins forever); the probe code
    itself shuts down cleanly on a plain loop."""
    async def scenario():
        server = await asyncio.start_server(_fake_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await mcs.probe(f"127.0.0.1:{port}", timeout=3.0)
        finally:
            server.close()
            await server.wait_closed()

    js, lat = asyncio.run(scenario())
    assert js["players"]["online"] == 2
    assert lat >= 0
    state, motd = mcs.classify(js)
    assert state == "online"
    assert motd == "FriendlyFire | Survival"


@pytest.mark.anyio
async def test_collect_shapes_offline_targets(anyio_backend):
    # Point collection at a definitely-closed port; must not raise.
    mcs._CACHE.update(ts=0.0, data=None)
    import os
    old = os.environ.get("FF_MC_STATUS_TARGETS")
    os.environ["FF_MC_STATUS_TARGETS"] = json.dumps([
        {"id": "dead", "label": "Dead", "address": "127.0.0.1:1"}])
    try:
        data = await mcs.collect(force=True)
    finally:
        if old is None:
            os.environ.pop("FF_MC_STATUS_TARGETS", None)
        else:
            os.environ["FF_MC_STATUS_TARGETS"] = old
    assert data["servers"][0]["state"] == "offline"
    assert data["servers"][0]["error"]
