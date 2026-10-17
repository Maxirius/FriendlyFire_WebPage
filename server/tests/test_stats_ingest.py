"""Tests for the StatsWebUI ingest (mocked HTTP)."""
import json
import time

import pytest

import db as dbm
import stats_ingest


@pytest.fixture()
def fresh_db(monkeypatch):
    tmpfile = "/tmp/ff_ing_test_%d.db" % (time.time_ns() % 10**9,)
    monkeypatch.setattr(dbm, "_DB_PATH", tmpfile)
    conn = dbm.connect(tmpfile)
    conn.executescript(dbm.SCHEMA)
    yield conn
    conn.close()
    import os
    os.unlink(tmpfile)


@pytest.mark.anyio
async def test_ingest_once_upserts(fresh_db, monkeypatch):
    payload_a = {"players": [{
        "name": "Alice", "uuid": "a" * 32, "playHours": 1.5,
        "advancements": {"done": 3, "total": 132},
        "stats": {"minecraft:mined": {"minecraft:stone": 42}},
    }]}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self): return payload_a

    class _FakeCli:
        async def get(self, url):
            return _FakeResp()

    async def _fake_cli():
        return _FakeCli()

    monkeypatch.setattr(stats_ingest, "_client", _fake_cli)

    rep = await stats_ingest.ingest_once()
    assert rep["ok"] and rep["playersUpserted"] == 1

    tx = dbm.Database(fresh_db)
    row = tx.one("SELECT * FROM ingested_players WHERE uuid=?", "a" * 32)
    assert row["name"] == "Alice"
    assert row["play_hours"] == 1.5
    assert row["advancements_done"] == 3

    # Upsert with CHANGED values updates in place (no duplicates).
    payload_a["players"][0]["playHours"] = 2.5
    await stats_ingest.ingest_once()
    cnt = tx.one("SELECT COUNT(*) c FROM ingested_players")["c"]
    assert cnt == 1
    row = tx.one("SELECT play_hours FROM ingested_players WHERE uuid=?", "a" * 32)
    assert row["play_hours"] == 2.5

    # Archived players shape
    plist = stats_ingest.archived_players()
    assert plist[0]["name"] == "Alice"
    assert plist[0]["raw"]["stats"]["minecraft:mined"]["minecraft:stone"] == 42


@pytest.mark.anyio
async def test_ingest_failure_does_not_raise(fresh_db, monkeypatch):
    class _Boom:
        def raise_for_status(self):
            raise RuntimeError("boom")

    class _FakeCli:
        async def get(self, url):
            return _Boom()

    async def _fake_cli():
        return _FakeCli()

    monkeypatch.setattr(stats_ingest, "_client", _fake_cli)
    rep = await stats_ingest.ingest_once()
    assert not rep["ok"]
    assert rep.get("error")
