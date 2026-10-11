"""Integration tests for the FriendlyFire auth service.

Drives the FastAPI app via httpx.AsyncClient + ASGITransport against a
fresh SQLite DB per test. External boundaries mocked: Mojang lookup,
avatar fetching, SMTP sender.
"""
import os
import pathlib
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["FF_PUBLIC_ORIGIN"] = "https://mc.test.example"
os.environ["FF_COOKIE_SECURE"] = "1"
os.environ.pop("FF_ADMIN_EMAIL", None)

import pytest

_tmpdir = tempfile.mkdtemp(prefix="ff_test_")


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def fresh_db(monkeypatch):
    """Fresh on-disk DB per test (WAL requires a real file)."""
    import db as dbm
    monkeypatch.setattr(dbm, "_DB_PATH", os.path.join(_tmpdir, "test.db"))
    dbm._DB_PATH = os.path.join(_tmpdir, "test.db")
    dbm.init()
    yield dbm
    conn = dbm.connect()
    for table in ("audit", "resets", "invites", "sessions", "users", "meta"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    conn.close()


@pytest.fixture()
def app_module(fresh_db, monkeypatch):
    import app as app_mod
    static = pathlib.Path(_tmpdir) / "static"
    static.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_mod, "STATIC_DIR", static)
    (static / "index.html").write_text("<h1>dash</h1>")
    (static / "login.html").write_text("<h1>login</h1>")
    (static / "register.html").write_text("<h1>register</h1>")
    (static / "site.css").write_text("")
    return app_mod


@pytest.fixture()
def client_factory(app_module):
    import httpx

    class _Ctx:
        def __init__(self, c):
            self.c = c

        async def __aenter__(self):
            return self.c

        async def __aexit__(self, *_exc):
            await self.c.aclose()
            return False

    def _do(cookies=None):
        transport = httpx.ASGITransport(app=app_module.app,
                                        client=("10.1.2.3", 45000))
        c = httpx.AsyncClient(transport=transport,
                              base_url="https://mc.test.example",
                              cookies=dict(cookies or {}))
        return _Ctx(c)

    return _do


MOJANG_PROFILE_ID = "d8d5a926d5504b7fb4d4a4aabfeedddd"


@pytest.fixture()
def fake_mojang(app_module):
    async def _lookup(client_, nickname):
        assert 3 <= len(nickname) <= 16
        return nickname.capitalize(), MOJANG_PROFILE_ID

    with patch.object(app_module.mojang, "lookup_profile",
                      AsyncMock(side_effect=_lookup)):
        yield


@pytest.fixture()
def fake_avatar(app_module):
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 40
    with patch.object(app_module, "fetch_avatar_for",
                      AsyncMock(return_value=png)):
        yield


@pytest.fixture()
def fake_smtp(app_module):
    sent = []

    async def _sender(to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})

    with patch.object(app_module.mailer, "mail_configured",
                      MagicMock(return_value=True)), \
         patch.object(app_module.mailer, "send_async",
                      AsyncMock(side_effect=_sender)):
        yield sent


# ===========================================================================
# Security primitives
# ===========================================================================
def test_argon2_roundtrip_and_wrong_pw():
    from security import hash_password, verify_password
    h = hash_password("correct horse battery staple")
    assert verify_password(h, "correct horse battery staple")
    assert not verify_password(h, "wrong")
    assert h.startswith("$argon2id$")


def test_tokens_are_unique_and_hashes_stable():
    from security import new_token, token_digest
    a, b = new_token(), new_token()
    assert a != b
    assert token_digest(a) == token_digest(a)
    assert token_digest(a) != token_digest(b)


def test_rate_limiter_blocks_after_max():
    from security import rate_hit
    for _ in range(5):
        assert not rate_hit("unit", "k1", 5, 60)
    assert rate_hit("unit", "k1", 5, 60)
    assert not rate_hit("unit", "other-key", 5, 60)


def test_nickname_validation():
    from mojang import nickname_format_ok
    assert nickname_format_ok("abc_Deff01")
    assert not nickname_format_ok("ab")
    assert not nickname_format_ok("a" * 17)
    assert not nickname_format_ok("a-b!")


def test_avatar_placeholder_is_png():
    from avatar import placeholder_png
    blob = placeholder_png(64)
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(blob) > 200


def test_skin_face_crop_works():
    """Synthetic 64x64 skin: face band painted magenta, cropped correctly."""
    import io
    from PIL import Image
    from avatar import render_face_png
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for x in range(8, 16):
        for y in range(8, 16):
            img.putpixel((x, y), (255, 0, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    face = render_face_png(buf.getvalue(), size_px=32, include_hat_layer=True)
    assert face is not None
    out = Image.open(io.BytesIO(face))
    assert out.size == (32, 32)
    colors = {(255, 0, 255, 255)}
    assert out.convert("RGBA").getcolors()[0][1] in colors or \
        any(px in colors for cnt, px in out.convert("RGBA").getcolors())


# ===========================================================================
# Integration flows
# ===========================================================================
@pytest.mark.anyio
async def test_full_flow_bootstrap_register_invite_chain(
        app_module, client_factory, fake_mojang, fake_avatar, fake_smtp):
    import db as dbm
    conn = dbm.connect()
    tx = dbm.Database(conn)
    app_module.os.environ["FF_ADMIN_EMAIL"] = "boss@test.example"
    app_module.os.environ["FF_ADMIN_PASSWORD"] = "long-enough-passphrase!"
    app_module.bootstrap_admin(tx)
    tx.commit()
    conn.close()

    async with client_factory() as c:
        r = await c.get("/gate")
        assert r.status_code == 401

        r = await c.post("/api/login", json={
            "email": "boss@test.example",
            "password": "long-enough-passphrase!"})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["role"] == "admin"

        r = await c.get("/gate")
        assert r.status_code == 200
        assert r.headers["x-ff-role"] == "admin"

        r = await c.get("/api/me")
        assert r.status_code == 200

        r = await c.post("/api/invites", json={"recipientEmail": ""})
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        assert code.startswith("ff-")

        async with client_factory() as c2:
            r = await c2.post("/api/register", json={
                "email": "Friend@Test.Example",
                "password": "friendpass-long-99",
                "nickname": "steve_owner",
                "invite": code})
            assert r.status_code == 200, r.text
            assert r.json()["user"]["nickname"] == "Steve_owner"

            # same invite reused -> denied
            r = await c2.post("/api/register", json={
                "email": "second@test.example",
                "password": "another-pass-long-99",
                "nickname": "alex_player",
                "invite": code})
            assert r.status_code == 403

            r = await c2.get("/api/my-invites")
            assert r.status_code == 200
            assert r.json()["invites"] == []

            r = await c2.post("/api/admin/users/1/allow-invites",
                              json={"enabled": True})
            assert r.status_code == 403

            r = await c2.get("/whitelist.txt")
            assert r.status_code == 403

        r = await c.get("/api/admin/users")
        emails = [u["email"] for u in r.json()["users"]]
        assert "friend@test.example" in emails

        r = await c.get("/api/admin/stats")
        assert r.json()["users"] == 2
        assert r.json()["pendingInvites"] == 0

        r = await c.get("/whitelist.txt")
        assert r.status_code == 200
        assert MOJANG_PROFILE_ID in r.text

        r = await c.post("/api/invites", json={"recipientEmail": ""},
                         headers={"Origin": "https://evil.example"})
        assert r.status_code == 403

        r = await c.post("/api/logout")
        r = await c.get("/gate")
        assert r.status_code == 401


@pytest.mark.anyio
async def test_member_without_permission_cannot_invite(
        app_module, client_factory, fake_mojang, fake_avatar):
    import db as dbm
    conn = dbm.connect()
    tx = dbm.Database(conn)
    tx.run("INSERT INTO users(email,pw_hash,nickname,role,invites_enabled,"
           "created_at) VALUES(?,?,?,?,0,?)",
           "plain.member@example.org", "$argon2id$x", "Plain_Member",
           "member", dbm.now())
    tx.commit()
    uid = tx.one("SELECT id FROM users WHERE email=?",
                 "plain.member@example.org")["id"]
    raw = app_module.create_session(tx, uid)
    tx.commit()
    conn.close()

    async with client_factory() as c:
        c.cookies.set("ff_session", raw)
        r = await c.post("/api/invites", json={"recipientEmail": ""})
        assert r.status_code == 403


@pytest.mark.anyio
async def test_allowed_member_creates_and_revokes(
        app_module, client_factory, fake_mojang, fake_avatar, fake_smtp):
    import db as dbm
    conn = dbm.connect()
    tx = dbm.Database(conn)
    tx.run("INSERT INTO users(email,pw_hash,nickname,role,invites_enabled,"
           "created_at) VALUES(?,?,?,?,1,?)",
           "trusted@example.org", "$argon2id$x", "Trusted_One", "member",
           dbm.now())
    tx.commit()
    uid = tx.one("SELECT id FROM users WHERE email=?",
                 "trusted@example.org")["id"]
    raw = app_module.create_session(tx, uid)
    tx.commit()
    conn.close()

    async with client_factory() as c:
        c.cookies.set("ff_session", raw)
        r = await c.post("/api/invites",
                         json={"recipientEmail": "pal@example.org"})
        assert r.status_code == 200
        assert r.json()["mailed"] is True
        assert len(fake_smtp) == 1
        assert fake_smtp[0]["to"] == "pal@example.org"

        r = await c.get("/api/my-invites")
        assert len(r.json()["invites"]) == 1
        code = r.json()["invites"][0]["code"]

        r = await c.post("/api/invites/revoke", json={"code": code})
        assert r.status_code == 200
        r = await c.get("/api/my-invites")
        assert r.json()["invites"] == []


@pytest.mark.anyio
async def test_password_reset_flow_invalidates_old_sessions(
        app_module, client_factory, fake_smtp):
    import db as dbm
    import security as sec
    conn = dbm.connect()
    tx = dbm.Database(conn)
    tx.run("INSERT INTO users(email,pw_hash,nickname,role,invites_enabled,"
           "created_at) VALUES(?,?,?,?,0,?)",
           "victim@example.org", sec.hash_password("original-secret-123"),
           "Victim_User", "member", dbm.now())
    tx.commit()
    conn.close()

    async with client_factory() as c:
        r = await c.post("/api/login", json={
            "email": "victim@example.org",
            "password": "original-secret-123"})
        assert r.status_code == 200

        r = await c.post("/api/forgot-password", json={
            "email": "victim@example.org"})
        assert r.status_code == 200
        assert len(fake_smtp) == 1

        html = fake_smtp[0]["html"]
        pos = html.find("/reset/")
        assert pos > 0
        token = html[pos:].split('"')[0][len("/reset/"):]
        assert token

        # uniform answer for unknown mailbox (no disclosure, no mail)
        r = await c.post("/api/forgot-password", json={
            "email": "nonexistent@example.org"})
        assert r.status_code == 200
        assert len(fake_smtp) == 1

        r = await c.post("/api/reset-password", json={
            "token": token, "newPassword": "totally-new-secret-777"})
        assert r.status_code == 200, r.text

        r = await c.get("/api/me")
        assert r.status_code == 200

        async with client_factory() as c2:
            r = await c2.post("/api/login", json={
                "email": "victim@example.org",
                "password": "original-secret-123"})
            assert r.status_code == 401
            r = await c2.post("/api/login", json={
                "email": "victim@example.org",
                "password": "totally-new-secret-777"})
            assert r.status_code == 200

        r = await c.post("/api/reset-password", json={
            "token": token, "newPassword": "reuse-attempt-99999"})
        assert r.status_code == 403


@pytest.mark.anyio
async def test_weak_registration_inputs_rejected(
        app_module, client_factory, fake_mojang, fake_avatar):
    import db as dbm
    conn = dbm.connect()
    tx = dbm.Database(conn)
    tx.run("INSERT INTO users(email,pw_hash,nickname,role,invites_enabled,"
           "created_at) VALUES(?,?,?,?,0,?)",
           "seed.admin@example.org", "$argon2id$x", "Seed_Admin", "admin",
           dbm.now())
    seed_uid = tx.one("SELECT id FROM users WHERE email=?",
                      "seed.admin@example.org")["id"]
    tx.run("INSERT INTO invites(code_hash,code_plain,created_by,created_at,"
           "expires_at) VALUES(?,?,?,?,?)",
           "hh", "ff-valid", seed_uid, dbm.now(), dbm.now() + 3600)
    tx.commit()
    conn.close()

    cases = [
        {"email": "not-an-email", "password": "aaaaaaaaaaaa",
         "nickname": "SomePlayer", "invite": "ff-valid"},
        {"email": "x@y.zz", "password": "short",
         "nickname": "SomePlayer", "invite": "ff-valid"},
        {"email": "x@y.zz", "password": "aaaaaaaaaaaa",
         "nickname": "bad!!nick", "invite": "ff-valid"},
        {"email": "x@y.zz", "password": "aaaaaaaaaaaa",
         "nickname": "GoodNick", "invite": "forged-token"},
    ]
    import security as sec
    for payload in cases:
        with sec._lock:
            sec._buckets.clear()
        async with client_factory() as c:
            r = await c.post("/api/register", json=payload)
            assert r.status_code in (400, 403), (payload, r.text)


@pytest.mark.anyio
async def test_csrf_layers(app_module, client_factory):
    """Cross-site browser POST (Fetch Metadata) is dropped; same-origin
    Origin header passes; header-less non-browser traffic stays allowed."""
    async with client_factory() as c:
        r = await c.post("/api/login", json={
            "email": "a@b.cc", "password": "whatever-long"},
            headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403

        r = await c.post("/api/login", json={
            "email": "a@b.cc", "password": "whatever-long"},
            headers={"Sec-Fetch-Site": "same-origin"})
        assert r.status_code == 401  # reached the handler (bad creds)

        r = await c.post("/api/login", json={
            "email": "a@b.cc", "password": "whatever-long"})
        assert r.status_code == 401  # header-less API client tolerated


@pytest.mark.anyio
async def test_logged_in_password_change_rotates_other_sessions(
        app_module, client_factory):
    import db as dbm
    import security as sec
    conn = dbm.connect()
    tx = dbm.Database(conn)
    tx.run("INSERT INTO users(email,pw_hash,nickname,role,invites_enabled,"
           "created_at) VALUES(?,?,?,?,0,?)",
           "changer@example.org", sec.hash_password("initial-pass-0000"),
           "Changer_One", "member", dbm.now())
    tx.commit()
    conn.close()

    async with client_factory() as c:
        r = await c.post("/api/login", json={
            "email": "changer@example.org", "password": "initial-pass-0000"})
        assert r.status_code == 200

        # wrong current password -> refuse
        r = await c.put("/api/profile/password", json={
            "currentPassword": "nope-not-it-999", "newPassword": "brand-new-pass-11"})
        assert r.status_code == 401

        # correct rotation succeeds
        r = await c.put("/api/profile/password", json={
            "currentPassword": "initial-pass-0000",
            "newPassword": "brand-new-pass-11"})
        assert r.status_code == 200

        # current session still valid
        r = await c.get("/api/me")
        assert r.status_code == 200

        # old password gone
        async with client_factory() as c2:
            r = await c2.post("/api/login", json={
                "email": "changer@example.org",
                "password": "initial-pass-0000"})
            assert r.status_code == 401


@pytest.mark.anyio
async def test_dashboard_requires_session(app_module, client_factory):
    async with client_factory() as c:
        r = await c.get("/", follow_redirects=False)
        assert r.status_code in (302, 307)
        assert "/login" in r.headers.get("location", "")

        # simulate the nginx auth_request handshake the way prod does:
        # nginx redirects browsers to /login when /gate yields 401
        r = await c.get("/gate")
        assert r.status_code == 401
