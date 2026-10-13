"""FriendlyFire authentication service.

Opaque server-side sessions (HttpOnly cookies), invite-only registration,
password reset via Mailcow SMTP, Minecraft nickname verification (Mojang)
with automatic skin-face avatars, member/admin invite management and a
whitelist export for the game server.

Runs behind nginx (auth_request gate); never publish the container port.
"""
import asyncio
import hmac
import json
import logging
import os
import pathlib
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import avatar as av
import db as dbm
import mailer
import mcstats_db
import mcstatus_service as mcstatus
import mojang
import security as sec
import activity
import stats_ingest

logging.basicConfig(level=os.environ.get("FF_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ff.auth")

BASE_URL = os.environ.get("FF_PUBLIC_ORIGIN", "https://mc.max-kuehn.com").rstrip("/")
TRUSTED_ORIGINS = {BASE_URL.lower()}
for _o in os.environ.get("FF_EXTRA_TRUSTED_ORIGINS", "").split(","):
    if _o.strip():
        TRUSTED_ORIGINS.add(_o.strip().rstrip("/").lower())

COOKIE_SECURE = os.environ.get("FF_COOKIE_SECURE", "1") == "1"
SESSION_TTL_S = int(os.environ.get("FF_SESSION_TTL_H", "168")) * 3600
INVITE_TTL_S = int(float(os.environ.get("FF_INVITE_TTL_H", "48")) * 3600)
RESET_TTL_S = int(float(os.environ.get("FF_RESET_TTL_MIN", "30")) * 60)
STATIC_DIR = pathlib.Path(os.environ.get("FF_STATIC_DIR", "/srv/web"))
BERLIN = ZoneInfo("Europe/Berlin")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")


def fmt_expiry(seconds_left: float) -> str:
    dt = datetime.now(BERLIN) + timedelta(seconds=max(0, seconds_left))
    suffix = "MESZ" if dt.dst() else "MEZ"
    return dt.strftime(f"%d.%m.%Y %H:%M {suffix}")


def client_ip(request: Request) -> str:
    xr = request.headers.get("x-real-ip", "")
    if xr:
        return xr.split(",")[0].strip()
    xf = request.headers.get("x-forwarded-for", "")
    if xf:
        return xf.split(",")[0].strip()
    return request.client.host if request.client else "-"


class ApiError(HTTPException):
    def __init__(self, code: str, status: int = 400):
        super().__init__(status_code=status, detail={"code": code})


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------
def get_db():
    conn = dbm.connect()
    tx = dbm.Database(conn)
    try:
        yield tx
        tx.commit()
    except Exception:
        tx.rollback()
        raise
    finally:
        conn.close()


DbDep = Annotated[dbm.Database, Depends(get_db)]

_shared_http: Optional[httpx.AsyncClient] = None


def http_client() -> httpx.AsyncClient:
    global _shared_http
    if _shared_http is None or _shared_http.is_closed:
        _shared_http = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"User-Agent": "FriendlyFire-Website/1.0"},
            follow_redirects=True,
        )
    return _shared_http


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
def _load_session_user(tx: dbm.Database, request: Request) -> Optional[Any]:
    raw = request.cookies.get(sec.SESSION_COOKIE, "")
    if not raw or len(raw) > 512:
        return None
    th = sec.token_digest(raw)
    sess = tx.one(
        "SELECT * FROM sessions WHERE token_hash=? AND revoked=0 AND expires_at>?",
        th, dbm.now(),
    )
    if not sess:
        return None
    user = tx.one("SELECT * FROM users WHERE id=?", sess["user_id"])
    if not user or user["disabled"]:
        return None
    if sess["expires_at"] < dbm.now() + SESSION_TTL_S / 2:
        tx.run("UPDATE sessions SET expires_at=? WHERE token_hash=?",
               dbm.now() + SESSION_TTL_S, th)
    return user


def create_session(tx: dbm.Database, user_id: int) -> str:
    raw = sec.new_token()
    tx.run(
        "INSERT INTO sessions(token_hash,user_id,created_at,expires_at)"
        " VALUES(?,?,?,?)",
        sec.token_digest(raw), user_id, dbm.now(), dbm.now() + SESSION_TTL_S,
    )
    return raw


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        sec.SESSION_COOKIE, raw_token, max_age=SESSION_TTL_S,
        httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.set_cookie(sec.SESSION_COOKIE, "", max_age=0, path="/",
                        httponly=True, secure=COOKIE_SECURE, samesite="lax")


async def current_user(request: Request, tx: DbDep) -> Any:
    user = _load_session_user(tx, request)
    if not user:
        raise ApiError("not_authenticated", 401)
    return user


async def maybe_current_user(request: Request, tx: DbDep) -> Optional[Any]:
    return _load_session_user(tx, request)


CurrentUser = Annotated[Any, Depends(current_user)]
OptCurrentUser = Annotated[Optional[Any], Depends(maybe_current_user)]


def require_admin(user: Any) -> Any:
    if user["role"] != "admin":
        raise ApiError("forbidden", 403)
    return user


# --------------------------------------------------------------------------
# Bootstrap + maintenance
# --------------------------------------------------------------------------
def bootstrap_admin(tx: dbm.Database) -> None:
    email = os.environ.get("FF_ADMIN_EMAIL", "")
    password = os.environ.get("FF_ADMIN_PASSWORD", "")
    nickname = os.environ.get("FF_ADMIN_NICK", "Admin")
    if not email or not password:
        log.info("bootstrap: FF_ADMIN_* unset, skipping")
        return
    if not EMAIL_RE.match(email) or not sec.password_policy_ok(password):
        log.error("bootstrap: admin email/password violate policy, refusing")
        return
    row = tx.one("SELECT id,role FROM users WHERE email=?", email)
    if row:
        if row["role"] != "admin":
            tx.run("UPDATE users SET role='admin', invites_enabled=1 WHERE id=?",
                   row["id"])
            log.info("bootstrap: promoted existing user #%s to admin", row["id"])
        return
    tx.run(
        "INSERT INTO users(email,pw_hash,nickname,role,invites_enabled,"
        " created_at) VALUES(?,?,?,?,1,?)",
        email, sec.hash_password(password), nickname, "admin", dbm.now(),
    )
    dbm.audit(tx, "bootstrap_admin", None, email)
    log.info("bootstrap: admin account created (%s)", email)


def purge_expired(tx: dbm.Database) -> None:
    now = dbm.now()
    tx.run("DELETE FROM sessions WHERE expires_at<? OR revoked=1", now)
    tx.run("DELETE FROM invites WHERE expires_at<? AND redeemed_at IS NULL", now)
    tx.run("DELETE FROM resets WHERE expires_at<? OR used_at IS NOT NULL", now)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Multiple uvicorn workers race on first-write WAL setup; tolerate it.
    last_exc: Optional[Exception] = None
    for attempt in range(5):
        try:
            dbm.init()
            break
        except Exception as exc:  # sqlite locked during sibling startup
            last_exc = exc
            await asyncio.sleep(1.0 + attempt)
    else:
        raise last_exc if last_exc else RuntimeError("db init failed")
    conn = dbm.connect()
    try:
        tx = dbm.Database(conn)
        bootstrap_admin(tx)
        purge_expired(tx)
        tx.commit()
    finally:
        conn.close()
    activity.start_sampler()
    stats_ingest.start_ingest()
    yield
    stats_ingest.stop_ingest()
    activity.stop_sampler()
    global _shared_http
    if _shared_http and not _shared_http.is_closed:
        await _shared_http.aclose()


app = FastAPI(title="FriendlyFire Auth", docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            hdrs = request.headers
            origin = (hdrs.get("origin") or "").strip().rstrip("/").lower()
            referer = (hdrs.get("referer") or "").strip()
            base = ""
            if referer:
                from urllib.parse import urlparse
                p = urlparse(referer)
                base = f"{p.scheme}://{p.netloc}".lower()
            if origin or base:
                if origin not in TRUSTED_ORIGINS and base not in TRUSTED_ORIGINS:
                    return JSONResponse({"detail": {"code": "bad_origin"}},
                                        status_code=403)
            else:
                # Layered defence (SameSite=Lax is the baseline): browsers
                # always attach Sec-Fetch-Site, so a cross-site posting is
                # provably attacker-driven and gets dropped. Header-less
                # non-browser traffic is unaffected.
                site = (hdrs.get("sec-fetch-site") or "").lower()
                if site == "cross-site":
                    return JSONResponse({"detail": {"code": "bad_origin"}},
                                        status_code=403)
    return await call_next(request)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def user_public(u: Any) -> dict:
    return {
        "id": u["id"],
        "email": u["email"],
        "nickname": u["nickname"],
        "uuid": u["nickname_uuid"],
        "role": u["role"],
        "invitesEnabled": bool(u["invites_enabled"]),
        "createdAt": u["created_at"],
        "lastLoginAt": u["last_login_at"],
        "hasAvatar": u["avatar"] is not None,
    }


VALID_METHODS_UNSAFE = ("POST", "PUT", "PATCH", "DELETE")


async def fetch_avatar_for(uuid_hex: str) -> Optional[bytes]:
    """Download skin texture and render the face; None on any failure."""
    try:
        client = http_client()
        prof = await client.get(
            f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid_hex}"
        )
        if prof.status_code != 200:
            return None
        props = prof.json().get("properties", [])
        tex_b64 = next((p["value"] for p in props if p["name"] == "textures"), None)
        if not tex_b64:
            return None
        import base64
        tex_meta = json.loads(base64.b64decode(tex_b64))
        skin_url = tex_meta.get("textures", {}).get("SKIN", {}).get("url")
        if not skin_url:
            return None
        skin_resp = await client.get(skin_url)
        if skin_resp.status_code != 200:
            return None
        return av.render_face_png(skin_resp.content, size_px=64)
    except Exception:
        log.exception("avatar fetch/render failed")
        return None


# --------------------------------------------------------------------------
# Health + auth gate
# --------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/gate")
async def auth_gate(request: Request, tx: DbDep):
    """nginx auth_request target: 200 = logged in, 401 = deny."""
    user = _load_session_user(tx, request)
    if not user:
        raise ApiError("not_authenticated", 401)
    return JSONResponse({"ok": True},
                        headers={"X-FF-User": str(user["id"]),
                                 "X-FF-Role": user["role"]})


# --------------------------------------------------------------------------
# Registration / login / logout
# --------------------------------------------------------------------------
class RegisterReq(BaseModel):
    email: str
    password: str
    nickname: str
    invite: str


@app.post("/api/register")
async def register(req: RegisterReq, request: Request, response: Response,
                   tx: DbDep):
    ip = client_ip(request)
    if sec.rate_hit("register", ip, 5, 3600):
        raise ApiError("rate_limited", 429)
    email = req.email.strip().lower()
    nickname = req.nickname.strip()
    if not EMAIL_RE.match(email):
        raise ApiError("invalid_email")
    if not sec.password_policy_ok(req.password):
        raise ApiError("weak_password")
    if not mojang.nickname_format_ok(nickname):
        raise ApiError("invalid_nick_format")

    digest = sec.token_digest(req.invite)
    invite = tx.one(
        "SELECT * FROM invites WHERE code_hash=? AND redeemed_at IS NULL"
        " AND expires_at>?", digest, dbm.now(),
    )
    if not invite:
        raise ApiError("invalid_invite", 403)

    dup = tx.one("SELECT 1 FROM users WHERE email=?", email)
    if dup:
        raise ApiError("email_taken", 409)

    try:
        profile = await mojang.lookup_profile(http_client(), nickname)
    except RuntimeError:
        raise ApiError("mojang_unavailable", 503)
    if profile is None:
        raise ApiError("unknown_nick", 400)
    nick_canonical, uuid_hex = profile

    clash = tx.one("SELECT 1 FROM users WHERE nickname=?", nick_canonical)
    if clash:
        raise ApiError("nick_taken", 409)

    avatar_png = await fetch_avatar_for(uuid_hex)

    import sqlite3 as _sq
    tx.immediate()
    try:
        # re-validate inside the exclusive transaction (race-safe redemption)
        fresh = tx.one("SELECT redeemed_at FROM invites WHERE code_hash=?",
                       digest)
        if not fresh or fresh["redeemed_at"] is not None:
            tx.rollback()
            raise ApiError("invalid_invite", 403)
        cursor = tx.run(
            "INSERT INTO users(email,pw_hash,nickname,nickname_uuid,avatar,"
            " role,invited_by,created_at,last_login_at)"
            " VALUES(?,?,?,?,?,'member',?,?,?)",
            email, sec.hash_password(req.password), nick_canonical, uuid_hex,
            avatar_png, invite["created_by"], dbm.now(), dbm.now(),
        )
        uid = cursor.lastrowid
        tx.run("UPDATE invites SET redeemed_at=?, redeemed_by=? WHERE"
               " code_hash=?", dbm.now(), uid, digest)
        raw_session = create_session(tx, uid)
        dbm.audit(tx, "register", uid, f"{email}|{nick_canonical}", ip)
        dbm.bump_whitelist_revision(tx)
        tx.commit()
    except _sq.IntegrityError as exc:
        tx.rollback()
        msg = str(exc).lower()
        if "nickname" in msg:
            raise ApiError("nick_taken", 409)
        raise ApiError("email_taken", 409)
    except Exception:
        tx.rollback()
        raise

    set_session_cookie(response, raw_session)
    return {"ok": True, "user": {
        "id": uid, "email": email, "nickname": nick_canonical,
        "role": "member",
    }}


class LoginReq(BaseModel):
    email: str
    password: str


@app.post("/api/login")
async def login(req: LoginReq, request: Request, response: Response,
                tx: DbDep):
    ip = client_ip(request)
    email_key = req.email.strip().lower()
    if sec.rate_hit("login_ip", ip, 10, 300):
        raise ApiError("rate_limited", 429)
    if sec.rate_hit("login_em", f"{ip}:{email_key}", 5, 900):
        raise ApiError("rate_limited", 429)

    user = tx.one("SELECT * FROM users WHERE email=?", email_key)
    ok = False
    if user and not user["disabled"]:
        ok = sec.verify_password(user["pw_hash"], req.password)
        if ok and sec.needs_rehash(user["pw_hash"]):
            tx.run("UPDATE users SET pw_hash=? WHERE id=?",
                   sec.hash_password(req.password), user["id"])

    if not ok:
        dbm.audit(tx, "login_fail", user["id"] if user else None, email_key, ip)
        tx.commit()
        raise ApiError("wrong_credentials", 401)

    raw_session = create_session(tx, user["id"])
    tx.run("UPDATE users SET last_login_at=? WHERE id=?", dbm.now(), user["id"])
    dbm.audit(tx, "login", user["id"], "", ip)
    tx.commit()
    set_session_cookie(response, raw_session)
    return {"ok": True, "user": user_public(user)}


@app.post("/api/logout")
async def logout(request: Request, response: Response, tx: DbDep):
    raw = request.cookies.get(sec.SESSION_COOKIE, "")
    if raw:
        tx.run("UPDATE sessions SET revoked=1 WHERE token_hash=?",
               sec.token_digest(raw))
        dbm.audit(tx, "logout", None, "")
    clear_session_cookie(response)
    return {"ok": True}


# --------------------------------------------------------------------------
# Password reset
# --------------------------------------------------------------------------
class ForgotReq(BaseModel):
    email: str


@app.post("/api/forgot-password")
async def forgot_password(req: ForgotReq, request: Request, tx: DbDep):
    ip = client_ip(request)
    email = req.email.strip().lower()
    if sec.rate_hit("forgot_ip", ip, 3, 900):
        return {"ok": True}  # silently swallow excess traffic
    if sec.rate_hit("forgot_em", email, 2, 900):
        return {"ok": True}

    user = tx.one("SELECT * FROM users WHERE email=? AND disabled=0", email)
    if user and mailer.mail_configured():
        raw = sec.new_token()
        tx.run(
            "INSERT INTO resets(token_hash,user_id,created_at,expires_at)"
            " VALUES(?,?,?,?)",
            sec.token_digest(raw), user["id"], dbm.now(),
            dbm.now() + RESET_TTL_S,
        )
        ttl_min = RESET_TTL_S / 60.0
        html = mailer.render_reset(BASE_URL, raw, ttl_min)
        try:
            await mailer.send_async(email, "FriendlyFire · Passwort-Reset",
                                    html)
            dbm.audit(tx, "forgot_sent", user["id"], "", ip)
        except Exception:
            log.exception("reset mail send failed")
            dbm.audit(tx, "forgot_send_error", user["id"], "", ip)
    tx.commit()
    # Uniform answer: never disclose whether the mailbox exists.
    return {"ok": True}


class ResetReq(BaseModel):
    token: str
    newPassword: str


@app.post("/api/reset-password")
async def reset_password(req: ResetReq, request: Request, response: Response,
                         tx: DbDep):
    ip = client_ip(request)
    if sec.rate_hit("reset", ip, 10, 3600):
        raise ApiError("rate_limited", 429)
    if not sec.password_policy_ok(req.newPassword):
        raise ApiError("weak_password")
    digest = sec.token_digest(req.token)
    row = tx.one(
        "SELECT * FROM resets WHERE token_hash=? AND used_at IS NULL"
        " AND expires_at>?", digest, dbm.now(),
    )
    if not row:
        raise ApiError("invalid_token", 403)
    tx.run("UPDATE users SET pw_hash=? WHERE id=?",
           sec.hash_password(req.newPassword), row["user_id"])
    tx.run("UPDATE resets SET used_at=? WHERE token_hash=?", dbm.now(), digest)
    tx.run("UPDATE sessions SET revoked=1 WHERE user_id=?", row["user_id"])
    raw_session = create_session(tx, row["user_id"])  # log in right away
    dbm.audit(tx, "reset_done", row["user_id"], "", ip)
    tx.commit()
    set_session_cookie(response, raw_session)
    return {"ok": True}


# --------------------------------------------------------------------------
# Profile: nickname + avatar
# --------------------------------------------------------------------------
class NicknameReq(BaseModel):
    nickname: str


@app.put("/api/profile/nickname")
async def change_nickname(req: NicknameReq, request: Request,
                          user: CurrentUser, tx: DbDep):
    nickname = req.nickname.strip()
    if not mojang.nickname_format_ok(nickname):
        raise ApiError("invalid_nick_format")
    try:
        profile = await mojang.lookup_profile(http_client(), nickname)
    except RuntimeError:
        raise ApiError("mojang_unavailable", 503)
    if profile is None:
        raise ApiError("unknown_nick", 400)
    canonical, uuid_hex = profile
    clash = tx.one("SELECT id FROM users WHERE nickname=? AND id<>?",
                   canonical, user["id"])
    if clash:
        raise ApiError("nick_taken", 409)
    avatar_png = await fetch_avatar_for(uuid_hex)
    tx.run("UPDATE users SET nickname=?, nickname_uuid=?, avatar=? WHERE id=?",
           canonical, uuid_hex, avatar_png, user["id"])
    dbm.audit(tx, "nickname_change", user["id"], canonical, client_ip(request))
    dbm.bump_whitelist_revision(tx)
    return {"ok": True, "nickname": canonical, "uuid": uuid_hex}


class PasswordChange(BaseModel):
    currentPassword: str
    newPassword: str


@app.put("/api/profile/password")
async def change_password(req: PasswordChange, request: Request,
                          user: CurrentUser, tx: DbDep):
    if not sec.verify_password(user["pw_hash"], req.currentPassword):
        raise ApiError("wrong_credentials", 401)
    if not sec.password_policy_ok(req.newPassword):
        raise ApiError("weak_password")
    tx.run("UPDATE users SET pw_hash=? WHERE id=?",
           sec.hash_password(req.newPassword), user["id"])
    # keep current session usable, invalidate everywhere else
    raw_now = request.cookies.get(sec.SESSION_COOKIE, "")
    if raw_now:
        tx.run("UPDATE sessions SET revoked=1 WHERE user_id=? AND"
               " token_hash<>?", user["id"], sec.token_digest(raw_now))
    dbm.audit(tx, "password_change", user["id"], "", client_ip(request))
    return {"ok": True}


@app.get("/avatar/{nickname}.png")
async def get_avatar(nickname: str, user: CurrentUser, tx: DbDep):
    row = tx.one("SELECT avatar FROM users WHERE nickname=?", nickname)
    if row and row["avatar"]:
        return Response(content=row["avatar"], media_type="image/png",
                        headers={"Cache-Control": "private, max-age=300"})
    return Response(content=av.placeholder_png(64), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=60"})


# --------------------------------------------------------------------------
# Invites
# --------------------------------------------------------------------------
class InviteReq(BaseModel):
    recipientEmail: str = ""


@app.post("/api/invites")
async def create_invite(req: InviteReq, request: Request, user: CurrentUser,
                        tx: DbDep):
    if user["role"] != "admin" and not user["invites_enabled"]:
        raise ApiError("forbidden", 403)
    if sec.rate_hit("invites", str(user["id"]), 20, 86400):
        raise ApiError("rate_limited", 429)

    recipient = req.recipientEmail.strip().lower()
    if recipient and not EMAIL_RE.match(recipient):
        raise ApiError("invalid_email")

    raw = sec.new_token("ff-")
    tx.run(
        "INSERT INTO invites(code_hash,code_plain,created_by,recipient_email,"
        " created_at,expires_at) VALUES(?,?,?,?,?,?)",
        sec.token_digest(raw), raw, user["id"], recipient or None, dbm.now(),
        dbm.now() + INVITE_TTL_S,
    )

    mailed = False
    if recipient and mailer.mail_configured():
        html = mailer.render_invite(BASE_URL, user["nickname"], raw,
                                    INVITE_TTL_S / 3600)
        try:
            await mailer.send_async(recipient,
                                    "FriendlyFire · Du wurdest eingeladen!",
                                    html)
            mailed = True
        except Exception:
            log.exception("invite mail send failed")

    dbm.audit(tx, "invite_created", user["id"],
              f"to:{recipient or '-'} mailed:{mailed}", client_ip(request))
    return {"ok": True, "code": raw,
            "url": f"{BASE_URL}/invite/{raw}", "mailed": mailed}


@app.get("/api/me")
async def me(user: CurrentUser):
    return {"user": user_public(user)}


@app.get("/api/my-invites")
async def my_invites(user: CurrentUser, tx: DbDep):
    rows = tx.all_rows(
        "SELECT code_plain, recipient_email, created_at, expires_at,"
        " redeemed_at FROM invites WHERE created_by=?"
        " ORDER BY created_at DESC LIMIT 100",
        user["id"],
    )
    return {"invites": [
        {"code": r["code_plain"], "url": f"{BASE_URL}/invite/{r['code_plain']}",
         "recipientEmail": r["recipient_email"], "createdAt": r["created_at"],
         "expiresAt": r["expires_at"], "redeemed": r["redeemed_at"] is not None}
        for r in rows
    ]}


class RevokeReq(BaseModel):
    code: str


@app.post("/api/invites/revoke")
async def revoke_invite(req: RevokeReq, request: Request, user: CurrentUser,
                        tx: DbDep):
    row = tx.one("SELECT * FROM invites WHERE code_plain=?", req.code)
    if not row:
        raise ApiError("not_found", 404)
    if user["role"] != "admin" and row["created_by"] != user["id"]:
        raise ApiError("forbidden", 403)
    tx.run("DELETE FROM invites WHERE code_plain=?", req.code)
    dbm.audit(tx, "invite_revoked", user["id"], "", client_ip(request))
    return {"ok": True}


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------
@app.get("/api/admin/users")
async def admin_users_real(user: CurrentUser, tx: DbDep):
    require_admin(user)
    rows = tx.all_rows(
        "SELECT id,email,nickname,role,invites_enabled,disabled,created_at,"
        " last_login_at FROM users ORDER BY created_at ASC")
    return {"users": [{
        "id": r["id"], "email": r["email"], "nickname": r["nickname"],
        "role": r["role"], "invitesEnabled": bool(r["invites_enabled"]),
        "disabled": bool(r["disabled"]), "createdAt": r["created_at"],
        "lastLoginAt": r["last_login_at"],
    } for r in rows]}


class ToggleReq(BaseModel):
    enabled: bool


@app.post("/api/admin/users/{user_id}/allow-invites")
async def admin_allow_invites(user_id: int, req: ToggleReq,
                              request: Request, user: CurrentUser, tx: DbDep):
    require_admin(user)
    target = tx.one("SELECT id FROM users WHERE id=?", user_id)
    if not target:
        raise ApiError("not_found", 404)
    tx.run("UPDATE users SET invites_enabled=? WHERE id=?",
           1 if req.enabled else 0, user_id)
    dbm.audit(tx, "admin_allow_invites", user["id"],
              f"user#{user_id}->{req.enabled}", client_ip(request))
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/disable")
async def admin_disable_user(user_id: int, req: ToggleReq,
                             request: Request, user: CurrentUser, tx: DbDep):
    """enabled=False bans the user: sessions killed + whitelist removal."""
    require_admin(user)
    if user_id == user["id"]:
        raise ApiError("cannot_self_disable", 400)
    target = tx.one("SELECT id FROM users WHERE id=?", user_id)
    if not target:
        raise ApiError("not_found", 404)
    banned = not req.enabled
    tx.run("UPDATE users SET disabled=? WHERE id=?",
           1 if banned else 0, user_id)
    if banned:
        tx.run("UPDATE sessions SET revoked=1 WHERE user_id=?", user_id)
    dbm.audit(tx, "admin_disable_user", user["id"],
              f"user#{user_id}->{'banned' if banned else 'reactivated'}",
              client_ip(request))
    dbm.bump_whitelist_revision(tx)
    return {"ok": True}


@app.get("/api/admin/invites")
async def admin_invites(user: CurrentUser, tx: DbDep):
    require_admin(user)
    rows = tx.all_rows(
        "SELECT i.*, u.nickname AS creator FROM invites i"
        " LEFT JOIN users u ON u.id=i.created_by ORDER BY i.created_at DESC"
        " LIMIT 200")
    return {"invites": [{
        "codePlain": r["code_plain"], "creator": r["creator"],
        "recipientEmail": r["recipient_email"], "createdAt": r["created_at"],
        "expiresAt": r["expires_at"], "redeemedBy": r["redeemed_by"],
    } for r in rows]}


@app.get("/api/admin/stats")
async def admin_stats(user: CurrentUser, tx: DbDep):
    require_admin(user)
    return {
        "users": tx.one("SELECT COUNT(*) c FROM users")["c"],
        "sessions": tx.one(
            "SELECT COUNT(*) c FROM sessions WHERE revoked=0 AND expires_at>? ",
            dbm.now())["c"],
        "pendingInvites": tx.one(
            "SELECT COUNT(*) c FROM invites WHERE redeemed_at IS NULL AND"
            " expires_at>?", dbm.now())["c"],
    }


# --------------------------------------------------------------------------
# Manual whitelist entries (extras) — also the hook for the future
# Discord-bot writer.
# --------------------------------------------------------------------------
class ExtraReq(BaseModel):
    nickname: str
    note: str = ""


@app.get("/api/admin/whitelist/extras")
async def list_extras(user: CurrentUser, tx: DbDep):
    require_admin(user)
    rows = tx.all_rows(
        "SELECT uuid,name,note,source,created_at FROM whitelist_extras"
        " ORDER BY created_at DESC")
    return {
        "revision": dbm.whitelist_revision(tx),
        "extras": [{
            "uuid": r["uuid"], "name": r["name"], "note": r["note"],
            "source": r["source"], "createdAt": r["created_at"],
        } for r in rows],
    }


@app.post("/api/admin/whitelist/extras")
async def add_extra(req: ExtraReq, request: Request, user: CurrentUser,
                    tx: DbDep):
    require_admin(user)
    nickname = req.nickname.strip()
    try:
        profile = await mojang.lookup_profile(http_client(), nickname)
    except RuntimeError:
        raise ApiError("mojang_unavailable", 503)
    if profile is None:
        raise ApiError("unknown_nick", 400)
    canonical, uuid_hex = profile
    clash_user = tx.one("SELECT 1 FROM users WHERE nickname_uuid=?",
                        uuid_hex)
    clash_extra = tx.one("SELECT 1 FROM whitelist_extras WHERE uuid=?",
                         uuid_hex)
    if clash_user or clash_extra:
        raise ApiError("nick_taken", 409)
    tx.run(
        "INSERT INTO whitelist_extras(uuid,name,added_by,source,note,"
        " created_at) VALUES(?,?,?,'manual',?,?)",
        uuid_hex, canonical, user["id"], req.note[:200], dbm.now(),
    )
    rev = dbm.bump_whitelist_revision(tx)
    dbm.audit(tx, "whitelist_extra_added", user["id"], canonical,
              client_ip(request))
    return {"ok": True, "uuid": uuid_hex, "name": canonical, "revision": rev}


@app.delete("/api/admin/whitelist/extras/{uuid_hex}")
async def remove_extra(uuid_hex: str, request: Request, user: CurrentUser,
                       tx: DbDep):
    require_admin(user)
    if not re.fullmatch(r"[0-9a-f]{32}", uuid_hex):
        raise ApiError("invalid_uuid", 400)
    deleted = tx.run("DELETE FROM whitelist_extras WHERE uuid=?",
                     uuid_hex).rowcount
    if not deleted:
        raise ApiError("not_found", 404)
    rev = dbm.bump_whitelist_revision(tx)
    dbm.audit(tx, "whitelist_extra_removed", user["id"], uuid_hex,
              client_ip(request))
    return {"ok": True, "revision": rev}


@app.get("/whitelist.txt")
async def whitelist_txt(request: Request, user: CurrentUser, tx: DbDep):
    require_admin(user)
    rows = tx.all_rows(
        "SELECT nickname_uuid FROM users WHERE disabled=0 AND nickname_uuid"
        " IS NOT NULL")
    extras = tx.all_rows("SELECT uuid FROM whitelist_extras")
    uuids = sorted({r["nickname_uuid"] for r in rows} |
                   {e["uuid"] for e in extras})
    dbm.audit(tx, "whitelist_export", user["id"], "",
              client_ip(request))
    return Response(content="\n".join(uuids) + "\n", media_type="text/plain")


SYNC_TOKEN = os.environ.get("FF_SYNC_TOKEN", "")


def require_sync_agent(request: Request) -> None:
    """Constant-time bearer check for the Velocity pull agent."""
    token = os.environ.get("FF_SYNC_TOKEN", "")
    if not token:
        raise ApiError("sync_disabled", 503)
    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {token}"
    if not hmac.compare_digest(supplied, expected):
        raise ApiError("forbidden", 403)


@app.get("/api/sync/whitelist")
async def sync_whitelist(request: Request, tx: DbDep):
    """Authoritative whitelist feed for the Velocity bridge plugin."""
    require_sync_agent(request)
    rows = tx.all_rows(
        "SELECT nickname, nickname_uuid FROM users"
        " WHERE disabled=0 AND nickname_uuid IS NOT NULL")
    extras = tx.all_rows(
        "SELECT uuid, name FROM whitelist_extras")
    merged: dict[str, str] = {}
    for r in rows:
        merged[r["nickname_uuid"]] = r["nickname"]
    for e in extras:
        merged[e["uuid"]] = e["name"]
    entries = [{"uuid": u, "name": n} for u, n in sorted(merged.items())]
    return JSONResponse(
        {"revision": dbm.whitelist_revision(tx), "entries": entries},
        headers={"Cache-Control": "no-store"})


@app.get("/ping")
async def ping(request: Request):
    """Tiny latency beacon for the in-page ping test (roundtrip timing).

    Echoes CORS headers for trusted origins so the page can also time a
    direct-to-RS beacon hostname (DNS-only, no Cloudflare in the path —
    mirroring the real client route onto port 25565).
    """
    headers = {"Cache-Control": "no-store"}
    origin = request.headers.get("origin", "")
    if origin and origin.lower() in TRUSTED_ORIGINS:
        headers.update({
            "Access-Control-Allow-Origin": origin,
            "Vary": "Origin",
        })
    return Response(status_code=204, headers=headers)


@app.get("/api/ping/beacons")
async def ping_beacons(user: CurrentUser):
    """Latency routes for the in-page ping test.

    Two routes mirror the two ways members reach the network:

    - ``door``: primary player door — a beacon hosted AT the game VM
      (published via OPNsense NAT, HTTPS), measuring exactly the
      client->home route. Requires FF_DOOR_BEACON_URL to be set.
    - ``rz``: detour via the RZ (Netcup Nuremberg) — the browser times a
      beacon terminating on the RS; the page ADDS the measured WireGuard
      tunnel transit (collector ``tunnelTransitMs``), since the beacon
      stops at the RS and never traverses the tunnel.

    Additional routes via FF_PING_BEACONS (JSON array {label,url}); every
    beacon endpoint must send CORS headers allowing this origin.
    """
    raw = os.environ.get("FF_PING_BEACONS", "").strip()
    beacons: list = []
    if raw:
        try:
            for b in json.loads(raw):
                beacons.append({
                    "id": str(b.get("id") or "extra"),
                    "label": str(b.get("label") or "?"),
                    "url": str(b.get("url") or ""),
                })
        except Exception:
            log.warning("FF_PING_BEACONS unparsable, ignoring")

    rz_url = os.environ.get(
        "FF_RZ_BEACON_URL", "https://ping.max-kuehn.com/ping").rstrip("/")
    door_url = os.environ.get("FF_DOOR_BEACON_URL", "").rstrip("/")
    if door_url:
        beacons.insert(0, {"id": "door",
                           "label": "Direkt zum Gameserver (Haustür)",
                           "url": door_url})

    # Tunnel transit (RS -> game VM via WireGuard, naked TCP connect) is
    # added to the RZ beacon measurement: that beacon ends at the RS and
    # never touches the tunnel, so the additive term completes the route.
    tunnel_ms = None
    try:
        data = await mcstatus.collect()
        tunnel_ms = data.get("tunnelTransitMs")
    except Exception:
        log.warning("tunnel transit unavailable for ping estimate")
    return {
        "relays": [],
        "beacons": beacons + [{
            "id": "rz",
            "label": "Bis Rechenzentrum Nürnberg (Detour)",
            "url": rz_url,
        }],
        "estimate": {"measuredTunnelMs": tunnel_ms},
    }


@app.get("/api/stats/ingame")
async def stats_ingame(user: CurrentUser, category: str = ""):
    """Ingame statistics from the RS archive (fed by the StatsWebUI pull).

    Mirrors the mod's board catalogue (icons included) but strips its
    ranking overlays (champion/crowns/grades): neutral best-lists only.
    With ?category=<id> only that category's boards are returned.
    """
    players = stats_ingest.archived_players()
    boards_registry = await _statswebui_boards()
    wanted = [c for c in boards_registry
              if c.get("id") != "crowns"   # scoring overlays stay out
              and (not category or c["id"] == category)]

    def board_rank(board_id: str, n: int = 10) -> list:
        """Neutral descending list over all players for one board."""
        rows = []
        for p in players:
            det = p.get("detail") or {}
            entry = (det.get("stats") or {}).get(board_id)
            if isinstance(entry, dict) and entry.get("value") is not None:
                rows.append((p["name"], float(entry["value"]),
                             entry.get("display") or str(entry["value"])))
        rows.sort(key=lambda r: (-r[1], r[0]))
        return [{"name": nm, "value": val, "display": disp}
                for nm, val, disp in rows[:n]]

    cats_payload = []
    for c in wanted:
        boards = []
        for b in c.get("boards", []):
            if not b.get("listed", True):
                continue
            bid = b["id"]
            boards.append({
                "id": bid, "unit": b.get("unit"),
                "icon": b.get("icon"),
                "lowerWins": bool(b.get("lowerWins")),
                "leaders": board_rank(bid),
            })
        cats_payload.append({"id": c["id"], "icon": c.get("icon"),
                             "boards": boards})

    # Generic "more" category: every vanilla custom stat not already
    # covered by a mod board becomes its own mini-leaderboard, ranked
    # straight from the archived vanilla data. This roughly doubles the
    # visible statistics without touching the mod.
    covered: set[str] = set()
    for c in cats_payload:
        covered.update(b["id"] for b in c["boards"])
    more_boards = _extra_custom_boards(players, covered)
    if more_boards and (not category or category == "more"):
        cats_payload.append({
            "id": "more",
            "icon": "minecraft:book",
            "boards": more_boards,
        })

    profiles = [{
        "uuid": p["uuid"], "name": p["name"],
        "playHours": p["playHours"],
        "advancementsDone": p["advancementsDone"],
        "advancementsTotal": p["advancementsTotal"],
    } for p in players]

    return JSONResponse({
        "profiles": profiles,
        "categories": cats_payload,
        "archive": stats_ingest.archive_health(),
    }, headers={"Cache-Control": "120"})


_LEADERBOARD_META: Optional[list] = None


# Maps vanilla custom stat ids to board ids + icons for the generic
# "more" category. Anything unmapped gets a sensible default icon.
_MORE_STAT_META = {
    "minecraft:aviate_one_cm": ("fly-distance", "minecraft:fireworks_rocket"),
    "minecraft:climb_one_cm": ("climb-distance", "minecraft:ladder"),
    "minecraft:crouch_one_cm": ("crouch-distance", "minecraft:string"),
    "minecraft:fall_one_cm": ("fall-distance", "minecraft:feather"),
    "minecraft:walk_on_water_one_cm": ("walk-water-distance", "minecraft:lily_pad"),
    "minecraft:walk_under_water_one_cm": ("dive-distance", "minecraft:turtle_helmet"),
    "minecraft:pig_one_cm": ("pig-distance", "minecraft:carrot_on_a_stick"),
    "minecraft:damage_absorbed": ("damage-absorbed", "minecraft:golden_apple"),
    "minecraft:damage_blocked_by_shield": ("damage-blocked", "minecraft:shield"),
    "minecraft:damage_taken": ("damage-taken", "minecraft:rotten_flesh"),
    "minecraft:damage_resisted": ("damage-resisted", "minecraft:potion"),
    "minecraft:damage_dealt_absorbed": ("damage-dealt-absorbed", "minecraft:golden_apple"),
    "minecraft:damage_dealt_resisted": ("damage-dealt-resisted", "minecraft:potion"),
    "minecraft:mob_kills": ("mob-kills", "minecraft:iron_sword"),
    "minecraft:bell_ring": ("bells-rung", "minecraft:bell"),
    "minecraft:eat_cake_slice": ("cake-eaten", "minecraft:cake"),
    "minecraft:play_record": ("records-played", "minecraft:music_disc_cat"),
    "minecraft:play_noteblock": ("noteblocks-played", "minecraft:note_block"),
    "minecraft:tune_noteblock": ("noteblocks-tuned", "minecraft:note_block"),
    "minecraft:pot_flower": ("flowers-potted", "minecraft:flower_pot"),
    "minecraft:open_barrel": ("barrels-opened", "minecraft:barrel"),
    "minecraft:open_shulker_box": ("shulkers-opened", "minecraft:shulker_box"),
    "minecraft:open_enderchest": ("enderchests-opened", "minecraft:ender_chest"),
    "minecraft:trigger_trapped_chest": ("trapped-chests-triggered", "minecraft:trapped_chest"),
    "minecraft:fill_cauldron": ("cauldrons-filled", "minecraft:cauldron"),
    "minecraft:use_cauldron": ("cauldrons-used", "minecraft:cauldron"),
    "minecraft:clean_armor": ("armor-cleaned", "minecraft:leather_helmet"),
    "minecraft:clean_banner": ("banners-cleaned", "minecraft:white_banner"),
    "minecraft:clean_shulker_box": ("shulkers-cleaned", "minecraft:shulker_box"),
    "minecraft:inspect_dispenser": ("dispensers-inspected", "minecraft:dispenser"),
    "minecraft:inspect_dropper": ("droppers-inspected", "minecraft:dropper"),
    "minecraft:inspect_hopper": ("hoppers-inspected", "minecraft:hopper"),
    "minecraft:interact_with_anvil": ("anvil-uses", "minecraft:anvil"),
    "minecraft:interact_with_beacon": ("beacon-uses", "minecraft:beacon"),
    "minecraft:interact_with_blast_furnace": ("blast-furnace-uses", "minecraft:blast_furnace"),
    "minecraft:interact_with_brewingstand": ("brewing-stands-used", "minecraft:brewing_stand"),
    "minecraft:interact_with_campfire": ("campfires-used", "minecraft:campfire"),
    "minecraft:interact_with_cartography_table": ("cartography-uses", "minecraft:cartography_table"),
    "minecraft:interact_with_crafting_table": ("crafting-uses", "minecraft:crafting_table"),
    "minecraft:interact_with_furnace": ("furnace-uses", "minecraft:furnace"),
    "minecraft:interact_with_grindstone": ("grindstone-uses", "minecraft:grindstone"),
    "minecraft:interact_with_lectern": ("lectern-uses", "minecraft:lectern"),
    "minecraft:interact_with_loom": ("loom-uses", "minecraft:loom"),
    "minecraft:interact_with_smithing_table": ("smithing-uses", "minecraft:smithing_table"),
    "minecraft:interact_with_smoker": ("smoker-uses", "minecraft:smoker"),
    "minecraft:interact_with_stonecutter": ("stonecutter-uses", "minecraft:stonecutter"),
    "minecraft:raid_trigger": ("raids-triggered", "minecraft:ominous_bottle"),
    "minecraft:target_hit": ("targets-hit", "minecraft:target"),
    "minecraft:sleep_in_straw_bed": ("straw-beds-slept", "minecraft:sweet_berries"),
    "minecraft:sneak_time": ("sneak-time", "minecraft:string"),
    "minecraft:leave_game": ("sessions-played", "minecraft:oak_door"),
    "minecraft:time_since_death": ("time-since-death", "minecraft:clock"),
    "minecraft:time_since_rest": ("time-since-rest", "minecraft:red_bed"),
    "minecraft:total_world_time": ("time-in-world", "minecraft:clock"),
    "minecraft:talked_to_villager": ("villager-talks", "minecraft:emerald"),
    "minecraft:traded_with_villager": ("villager-deals", "minecraft:emerald"),
}

_LOWER_WIN_BOARDS = {"time-since-death", "time-since-rest", "damage-taken"}


def _extra_custom_boards(players: list, covered: set[str]) -> list:
    """Builds generic boards for uncovered vanilla custom stats.

    Returns [] when the archive carries no vanilla detail (old feeds).
    """
    seen: dict[str, dict] = {}
    for p in players:
        det = p.get("detail") or {}
        for e in (det.get("vanilla") or {}).get("custom", []):
            sid = e.get("id")
            if not sid or not isinstance(e.get("value"), (int, float)):
                continue
            board_id, icon = _MORE_STAT_META.get(sid, (None, None))
            if board_id is None:
                # derive: minecraft:foo_bar -> foo-bar
                board_id = sid.replace("minecraft:", "").replace("_", "-")
                icon = "minecraft:paper"
            if board_id in covered:
                continue
            agg = seen.setdefault(board_id, {
                "id": board_id, "unit": e.get("unit") or "count",
                "icon": icon, "lowerWins": board_id in _LOWER_WIN_BOARDS,
                "rows": [],
            })
            agg["rows"].append((p["name"], float(e["value"]),
                                e.get("display") or str(e["value"])))
    out = []
    for b in seen.values():
        rows = sorted(b.pop("rows"), key=lambda r: (-r[1], r[0]))
        b["leaders"] = [{"name": nm, "value": v, "display": d}
                        for nm, v, d in rows[:10]]
        out.append(b)
    return sorted(out, key=lambda b: b["id"])


async def _statswebui_boards() -> list:
    """Cached board registry from the mod (falls back gracefully)."""
    global _LEADERBOARD_META
    if _LEADERBOARD_META is not None:
        return _LEADERBOARD_META
    try:
        cli = await stats_ingest._client()
        r = await cli.get(f"{stats_ingest._source_base()}/api/leaderboards")
        if r.status_code == 200:
            meta = r.json().get("categories", [])
            if meta:
                _LEADERBOARD_META = meta
                return meta
    except Exception:
        log.warning("could not fetch StatsWebUI board registry")
    return _fallback_boards()


# Static board catalogue mirroring the mod's registry. Used when the mod
# is unreachable (paused instance) so the stats page never goes blank.
_FALLBACK_ICONS = {
    "__cat_advancements": "minecraft:enchanted_book",
    "__cat_life": "minecraft:experience_bottle",
    "__cat_exploration": "minecraft:compass",
    "__cat_mining": "minecraft:diamond_pickaxe",
    "__cat_deaths": "minecraft:skeleton_skull",
    "__cat_combat": "minecraft:iron_sword",
    "__cat_misc": "minecraft:name_tag",
    "advancements": "minecraft:enchanted_book", "play-time": "minecraft:clock",
    "animals-bred": "minecraft:wheat", "fish-caught": "minecraft:fishing_rod",
    "villager-trades": "minecraft:emerald", "items-enchanted": "minecraft:enchanted_book",
    "sleeps": "minecraft:red_bed", "jumps": "minecraft:rabbit_foot",
    "distance-total": "minecraft:compass", "distance-walk-sprint": "minecraft:sugar",
    "distance-walk": "minecraft:leather_boots", "distance-sprint": "minecraft:sugar",
    "distance-swim": "minecraft:heart_of_the_sea", "distance-boat": "minecraft:oak_boat",
    "distance-elytra": "minecraft:elytra", "distance-horse": "minecraft:saddle",
    "distance-happy-ghast": "minecraft:happy_ghast_spawn_egg",
    "distance-nautilus": "minecraft:nautilus_shell", "distance-minecart": "minecraft:minecart",
    "distance-strider": "minecraft:warped_fungus_on_a_stick",
    "chests-opened": "minecraft:compass", "blocks-mined": "minecraft:diamond_pickaxe",
    "ores": "minecraft:iron_ore", "diamonds-mined": "minecraft:diamond",
    "iron-mined": "minecraft:iron_ingot", "ancient-debris": "minecraft:ancient_debris",
    "logs-mined": "minecraft:oak_log", "sculk-mined": "minecraft:sculk",
    "tools-broken": "minecraft:flint", "deaths": "minecraft:skeleton_skull",
    "killed-by-zombie": "minecraft:zombie_head", "killed-by-creeper": "minecraft:creeper_head",
    "killed-by-skeleton": "minecraft:skeleton_skull", "killed-by-warden": "minecraft:echo_shard",
    "hostile-kills": "minecraft:iron_sword", "farm-kills": "minecraft:cooked_chicken",
    "zombies": "minecraft:zombie_head", "skeletons": "minecraft:skeleton_skull",
    "creepers": "minecraft:creeper_head", "endermen": "minecraft:ender_pearl",
    "blazes": "minecraft:blaze_rod", "wither-skeletons": "minecraft:wither_skeleton_skull",
    "ender-dragon": "minecraft:dragon_egg", "wither": "minecraft:nether_star",
    "warden": "minecraft:echo_shard", "elder-guardian": "minecraft:elder_guardian_spawn_egg",
    "bosses": "minecraft:nether_star", "raids-won": "minecraft:emerald",
    "player-kills": "minecraft:diamond_sword", "damage-dealt": "minecraft:iron_sword",
    "kills-per-hour": "minecraft:iron_sword",
}


def _fallback_boards() -> list:
    """Deterministic catalogue derived from the archived player details."""
    players = stats_ingest.archived_players(limit=1)
    cats: dict[str, list] = {}
    units: dict[tuple[str, str], tuple[str, bool]] = {}
    lower_win_ids = {"deaths", "killed-by-zombie", "killed-by-creeper",
                     "killed-by-skeleton", "killed-by-warden"}
    for p in players:
        det = p.get("detail") or {}
        for bid, entry in (det.get("stats") or {}).items():
            cat = entry.get("category") or "misc"
            if cat == "crowns":
                continue  # mod scoring overlays stay out (design rule)
            unit = entry.get("unit") or ""
            units[(bid, cat)] = (unit, bid in lower_win_ids)
            cats.setdefault(cat, []).append(bid)
    out = []
    for cid, bids in cats.items():
        out.append({
            "id": cid,
            "icon": _FALLBACK_ICONS.get("__cat_" + cid,
                                        _FALLBACK_ICONS.get(cid)),
            "boards": [{
                "id": bid,
                "unit": units.get((bid, cid), ("", False))[0],
                "icon": _FALLBACK_ICONS.get(bid),
                "lowerWins": units.get((bid, cid), ("", False))[1],
                "listed": True,
            } for bid in sorted(set(bids))],
        })
    return out


@app.get("/api/stats/player/{name}")
async def stats_player(name: str, user: CurrentUser):
    """Full stats profile for one player, trimmed for transport.

    Deep vanilla sections (mined/crafted/killed/...) keep only nonzero
    entries — zeros dominate the raw arrays (~1600 items each).
    """
    players = stats_ingest.archived_players(limit=500)
    for p in players:
        if p["name"].lower() == name.lower():
            det = p.get("detail") or {}
            vanilla = det.get("vanilla") or {}

            def trim(section: list) -> list:
                return [
                    {"id": e.get("id"),
                     "value": e.get("value"),
                     "display": e.get("display")}
                    for e in section
                    if isinstance(e, dict) and (e.get("value") or 0) != 0
                ]

            deep = {k: trim(v) for k, v in vanilla.items()
                    if isinstance(v, list)}
            stats_neutral = {
                bid: entry for bid, entry in (det.get("stats") or {}).items()
                if (entry or {}).get("category") != "crowns"
            }
            return JSONResponse({
                "name": p["name"], "uuid": p["uuid"],
                "playHours": p["playHours"],
                "advancementsDone": p["advancementsDone"],
                "advancementsTotal": p["advancementsTotal"],
                "advancementIds": ((det.get("advancements") or {}).get("ids")),
                "stats": stats_neutral,
                "deep": deep,
                "fetchedAt": p["fetchedAt"],
            })
    raise HTTPException(status_code=404, detail="player not found")


@app.get("/api/stats/compare")
async def stats_compare(a: str, b: str, user: CurrentUser):
    """Side-by-side comparison of two archived players."""
    async def one(nm: str) -> Optional[dict]:
        for p in stats_ingest.archived_players(limit=500):
            if p["name"].lower() == nm.lower():
                return p
        return None

    pa, pb = await one(a), await one(b)
    if not pa or not pb:
        raise HTTPException(status_code=404, detail="player(s) not found")

    def slim(p: dict) -> dict:
        det = p.get("detail") or {}
        return {
            "name": p["name"], "uuid": p["uuid"],
            "playHours": p["playHours"],
            "advancementsDone": p["advancementsDone"],
            "advancementsTotal": p["advancementsTotal"],
            "stats": {bid: e for bid, e in (det.get("stats") or {}).items()
                      if (e or {}).get("category") != "crowns"},
        }

    return JSONResponse({"a": slim(pa), "b": slim(pb)})


@app.get("/api/stats/live-events")
async def stats_live_events(user: CurrentUser, player: str = "",
                           kind: str = "", limit: int = 100):
    """Live event stream from the MCStats-to-SQL mod (needs credentials).

    Returns pvp_kill / death / join / leave events with resolved player
    names. Gracefully reports availability:false when the mod database
    is not configured or unreachable, so the frontend can adapt.
    """
    if not mcstats_db.available():
        return JSONResponse({"available": False, "events": []})

    def uuid_name_map(uuids: set[str]) -> dict[str, str]:
        """Resolve uuids to names via the mod players table, falling
        back to the local archive."""
        out: dict[str, str] = {}
        if uuids:
            try:
                for row in mcstats_db._query(
                        "SELECT uuid, name FROM players"):
                    if row["uuid"] in uuids:
                        out[row["uuid"]] = row["name"]
            except Exception:
                pass
        for p in stats_ingest.archived_players(limit=500):
            if p["uuid"] in uuids and p["uuid"] not in out:
                out[p["uuid"]] = p["name"]
        return out

    try:
        pu = None
        if player:
            row = mcstats_db.player_by_name(player)
            if not row:
                raise HTTPException(status_code=404,
                                     detail="player not found")
            pu = row["uuid"]
        rows = mcstats_db.events(player_uuid=pu, kind=kind or None,
                                 limit=limit)
        names = uuid_name_map({r["player_uuid"] for r in rows}
                              | {r["opponent_uuid"] for r in rows
                                 if r["opponent_uuid"]})
        return JSONResponse({
            "available": True,
            "events": [{
                "ts": r["ts"], "kind": r["kind"],
                "player": r["player_uuid"],
                "playerName": names.get(r["player_uuid"], "?"),
                "opponent": r["opponent_uuid"],
                "opponentName": names.get(r["opponent_uuid"])
                                if r["opponent_uuid"] else None,
                "detail": r["detail"],
            } for r in rows],
        })
    except HTTPException:
        raise
    except mcstats_db.DbMissing:
        return JSONResponse({"available": False, "events": [],
                             "error": "waiting-for-mod"})
    except Exception as exc:
        log.warning("live-events query failed: %s", exc)
        return JSONResponse({"available": False, "events": [],
                             "error": "db-unreachable"})


@app.get("/api/stats/pvp/{name}")
async def stats_pvp(name: str, user: CurrentUser):
    """PvP record for one player: kills/deaths per opponent."""
    if not mcstats_db.available():
        return JSONResponse({"available": False, "records": []})
    try:
        row = mcstats_db.player_by_name(name)
        if not row:
            raise HTTPException(status_code=404, detail="player not found")
        recs = mcstats_db.pvp_summary(row["uuid"])
        names: dict[str, str] = {}
        try:
            for p in mcstats_db._query("SELECT uuid, name FROM players"):
                names[p["uuid"]] = p["name"]
        except Exception:
            pass
        for p in stats_ingest.archived_players(limit=500):
            names.setdefault(p["uuid"], p["name"])
        return JSONResponse({
            "available": True,
            "records": [{
                "side": r["side"],          # 'kills' | 'deaths_by'
                "opponent": r["other_uuid"],
                "opponentName": names.get(r["other_uuid"], "?"),
                "count": r["cnt"],
            } for r in recs],
        })
    except HTTPException:
        raise
    except mcstats_db.DbMissing:
        return JSONResponse({"available": False, "records": [],
                             "error": "waiting-for-mod"})
    except Exception as exc:
        log.warning("pvp query failed: %s", exc)
        return JSONResponse({"available": False, "records": [],
                             "error": "db-unreachable"})


@app.get("/api/stats/community")
async def stats_community(user: CurrentUser, tx: DbDep,
                          hours: int = 24):
    """Aggregated statistics for the welcome page's statistics section."""
    hours = max(1, min(hours, 24 * 14))
    acts = await activity.activity_series(hours=hours)
    return JSONResponse(
        {"community": activity.community_counts(tx), "activity": acts},
        headers={"Cache-Control": "60"})


@app.get("/api/status/minecraft")
async def minecraft_status(user: CurrentUser, force: bool = False):
    """Live status of the game servers (survival/creative/network).

    Members-only (matches the rest of the site; the /api/ nginx location
    has no auth_request, so the guard lives here). Polled by the welcome
    page's status block; cached briefly server-side.
    """
    data = await mcstatus.collect(force=force)
    return JSONResponse(data, headers={
        "Cache-Control": f"max-age={data['nextRefreshSec']}",
    })


# --------------------------------------------------------------------------
# Deep-link redirects (sent in mails)
# --------------------------------------------------------------------------
@app.get("/invite/{raw}")
async def invite_link(raw: str):
    if len(raw) > 128 or not raw.startswith("ff-"):
        return RedirectResponse("/register")
    return RedirectResponse(f"/register?invite={raw}")


@app.get("/reset/{raw}")
async def reset_link(raw: str):
    if len(raw) > 128:
        return RedirectResponse("/forgot")
    return RedirectResponse(f"/reset?token={raw}")


# --------------------------------------------------------------------------
# Catch-all static (development convenience; prod nginx serves files)
# --------------------------------------------------------------------------
_SAFE_EXT = {".html", ".css", ".js", ".png", ".ico", ".txt", ".woff2"}
_EXCLUDE = ("/api/", "/gate", "/health", "/invite/", "/reset/",
            "/whitelist.txt", "/avatar/")


_PROTECTED_PAGES = {"", "index.html", "dashboard.html", "admin.html",
                    "stats.html"}


@app.get("/{rest:path}")
@app.head("/{rest:path}")
async def static_fallback(rest: str, request: Request, tx: DbDep):
    norm = rest.strip("/")
    if not norm or norm.endswith("/"):
        candidate = STATIC_DIR / norm / "index.html"
    else:
        candidate = STATIC_DIR / norm
    try:
        rp = candidate.resolve(strict=True)
    except (OSError, FileNotFoundError):
        return RedirectResponse("/login")
    if not rp.is_relative_to(STATIC_DIR.resolve()):
        return RedirectResponse("/login")
    if rp.suffix.lower() not in _SAFE_EXT:
        return RedirectResponse("/login")
    # Defense in depth: protected pages never leave the app without a
    # session, even when someone reaches the container past nginx.
    leaf = rp.name if norm else "index.html"
    if leaf in _PROTECTED_PAGES:
        if not _load_session_user(tx, request):
            return RedirectResponse("/login")
    return FileResponse(rp)
