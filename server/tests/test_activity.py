"""Tests for the activity sampler and statistics aggregation."""
import asyncio
import time

import pytest

import db as dbm
import activity


@pytest.fixture()
def fresh_db(monkeypatch):
    tmpfile = "/tmp/ff_act_test_%d.db" % (time.time_ns() % 10**9,)
    monkeypatch.setattr(dbm, "_DB_PATH", tmpfile)
    conn = dbm.connect(tmpfile)
    conn.executescript(dbm.SCHEMA)
    yield conn
    conn.close()
    import os
    os.unlink(tmpfile)


def _seed(conn, offsets_hours, surv, crea, net):
    tx = dbm.Database(conn)
    base = time.time() - offsets_hours * 3600
    for i, (s, c, n) in enumerate(zip(surv, crea, net)):
        ts = base + i * dbm.SAMPLE_INTERVAL_S
        tx.run("INSERT OR REPLACE INTO activity_samples"
               " (ts,players_survival,players_creative,players_network)"
               " VALUES(?,?,?,?)", ts, s, c, n)
    tx.commit()


@pytest.mark.anyio
async def test_take_sample_inserts_bucket(fresh_db, monkeypatch):
    monkeypatch.setattr(activity, "dbm", dbm)
    # Force the collector to see a canned status payload.
    canned = {"servers": [
        {"id": "survival", "playersOnline": 3},
        {"id": "creative", "playersOnline": 1},
        {"id": "network", "playersOnline": 4},
    ]}

    class _FakeMc:
        async def collect(self, force=False):
            return canned

    monkeypatch.setattr(activity.mcstatus, "collect",
                        _FakeMc().collect)
    await activity._take_sample()
    tx = dbm.Database(fresh_db)
    row = tx.one("SELECT * FROM activity_samples")
    assert row is not None
    assert row["players_survival"] == 3
    assert row["players_creative"] == 1
    assert row["players_network"] == 4
    # Second run replaces the same bucket (idempotent per interval).
    await activity._take_sample()
    cnt = tx.one("SELECT COUNT(*) c FROM activity_samples")["c"]
    assert cnt == 1


@pytest.mark.anyio
async def test_series_hourly_average_and_peaks(fresh_db, monkeypatch):
    # Seed 12 samples spanning 1 hour: survivors oscillate 0..4.
    offs = 0.5
    _seed(fresh_db, offs, [0, 2, 4, 3, 1, 0, 4, 2, 0, 1, 3, 4],
          [0] * 12, [1, 1, 2, 2, 3, 3, 4, 4, 3, 2, 1, 1])
    monkeypatch.setattr(activity, "dbm", dbm)
    data = await activity.activity_series(hours=2)
    assert data["peaks"]["survival"] == 4
    assert data["peaks"]["network"] == 4
    assert data["averages"]["survival"] == round(sum([0, 2, 4, 3, 1, 0, 4, 2, 0, 1, 3, 4]) / 12, 2)
    assert 1 <= len(data["series"]) <= 2     # spans at most two hour buckets
    assert data["coverageSamples"] == 12


@pytest.mark.anyio
async def test_retention_cleanup(fresh_db, monkeypatch):
    tx = dbm.Database(fresh_db)
    ancient = time.time() - dbm.SAMPLE_RETENTION_S - 7200
    tx.run("INSERT OR REPLACE INTO activity_samples"
           " (ts,players_survival,players_creative,players_network)"
           " VALUES(?,?,?,?)", ancient, 9, 9, 9)
    tx.commit()
    canned = {"servers": [
        {"id": "survival", "playersOnline": 0},
        {"id": "creative", "playersOnline": 0},
        {"id": "network", "playersOnline": 0},
    ]}

    class _FakeMc:
        async def collect(self, force=False):
            return canned

    monkeypatch.setattr(activity.mcstatus, "collect", _FakeMc().collect)
    monkeypatch.setattr(activity, "dbm", dbm)
    await activity._take_sample()
    cnt = tx.one("SELECT COUNT(*) c FROM activity_samples")["c"]
    assert cnt == 1        # ancient row purged, only the fresh bucket remains

def test_community_counts(fresh_db):
    tx = dbm.Database(fresh_db)
    tx.run("INSERT INTO users(email,pw_hash,nickname,role,created_at)"
           " VALUES('a@x.test','h','AAA','member',?)", dbm.now())
    tx.run("INSERT INTO users(email,pw_hash,nickname,role,disabled,created_at)"
           " VALUES('b@x.test','h','BBB','member',1,?)", dbm.now())
    tx.run("INSERT INTO whitelist_extras(uuid,name,created_at)"
           " VALUES('" + "a"*32 + "','BotGuy',?)", dbm.now())
    tx.commit()
    cc = activity.community_counts(tx)
    assert cc["membersActive"] == 1
    assert cc["whitelistedPlayers"] == 1
    assert cc["pendingInvites"] == 0
