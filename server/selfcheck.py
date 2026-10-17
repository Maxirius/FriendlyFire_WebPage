#!/usr/bin/env python3
"""Self-check for the FriendlyFire website (runs ON the RS).

Mints a short-lived admin session directly via the app's own machinery
(no password handling), walks every page + API endpoint through the
REAL public domain (nginx -> auth -> app), and reports deviations.

Usage on the RS:  sudo docker exec -w /srv/auth minecraft_auth python /srv/check/selfcheck.py
(or: sudo docker cp into the container first)
"""
import json
import sys
import time

import httpx

sys.path.insert(0, "/srv/auth")
import db as dbm  # noqa: E402
import app as apm  # noqa: E402
import security as sec  # noqa: E402

BASE = "https://mc.example.com"
UA = {"User-Agent": "FriendlyFire-SelfCheck/1.0"}

RESULTS = []


def check(kind, name, cond, detail=""):
    RESULTS.append({"kind": kind, "name": name,
                    "ok": bool(cond), "detail": detail})


def main() -> int:
    conn = dbm.connect()
    tx = dbm.Database(conn)
    admin = tx.one("SELECT * FROM users WHERE role=?", "admin")
    if not admin:
        print("FATAL: no admin account", file=sys.stderr)
        return 2
    raw = apm.create_session(tx, admin["id"])
    tx.commit()
    conn.close()
    cookie = {sec.SESSION_COOKIE: raw}

    c = httpx.Client(cookies=cookie, follow_redirects=False, timeout=20,
                     headers=UA)

    # ---- pages (logged in) --------------------------------------------------
    pages_expect = {
        "/": ["serverstatus", "modShowcase", "statFigures"],
        "/stats": ["catScroll", "viewBody", "viewHead"],
        "/dashboard.html": ["invPanel", "admPanel"],
        "/admin": ["usrRows", "extraRows", "scUsers"],
        "/login": [],
    }
    for path, needles in pages_expect.items():
        r = c.get(BASE + path)
        body = r.text
        check("page", path, r.status_code == 200,
              f"http {r.status_code}")
        for needle in needles:
            check("markup", f"{path} contains {needle}",
                  needle in body,
                  "missing" if needle not in body else "")

    # ---- anonymous gating ---------------------------------------------------
    anon = httpx.Client(follow_redirects=False, timeout=20, headers=UA)
    for path in ("/", "/stats", "/dashboard.html", "/admin"):
        r = anon.get(BASE + path)
        check("gate", f"anon {path} -> 302", r.status_code == 302,
              f"http {r.status_code}")
    r = anon.get(BASE + "/api/stats/ingame")
    check("gate", "anon /api/stats/ingame -> 401", r.status_code == 401,
          f"http {r.status_code}")
    r = anon.get(BASE + "/ping")
    check("gate", "anon /ping -> 204", r.status_code == 204,
          f"http {r.status_code}")

    # ---- APIs (logged in) ----------------------------------------------------
    apis = {
        "/api/me": 200,
        "/api/status/minecraft": 200,
        "/api/stats/community": 200,
        "/api/stats/ingame": 200,
        "/api/ping/beacons": 200,
        "/api/my-invites": 200,
    }
    for path, want in apis.items():
        r = c.get(BASE + path)
        check("api", path, r.status_code == want,
              f"http {r.status_code} (want {want})")

    # ---- new stats endpoints (player profile + compare) ----------------------
    r = c.get(BASE + "/api/stats/ingame")
    profiles = r.json().get("profiles", []) if r.status_code == 200 else []
    if profiles:
        pname = profiles[0]["name"]
        r = c.get(BASE + f"/api/stats/player/{pname}")
        check("api", f"/api/stats/player/{pname}",
              r.status_code == 200 and "stats" in r.text,
              f"http {r.status_code}")
        r = c.get(BASE + f"/api/stats/compare?a={pname}&b={pname}")
        check("api", f"/api/stats/compare ({pname})", r.status_code == 200,
              f"http {r.status_code}")

    # ---- semantic sanity on the ingame payload -------------------------------
    r = c.get(BASE + "/api/stats/ingame")
    if r.status_code == 200:
        d = r.json()
        cats = d.get("categories", [])
        check("semantic", "ingame categories present", len(cats) >= 5,
              f"{len(cats)} cats")
        boards_with_data = sum(
            1 for cat in cats for b in cat.get("boards", []) if b.get("leaders"))
        check("semantic", "some boards have leaders", boards_with_data > 0,
              f"{boards_with_data} boards with data")
        check("semantic", "no crown/grade leakage",
              "championScore" not in r.text and "grades" not in r.text,
              "ranking overlay leaked!")

    # ---- status payload reflects reality -------------------------------------
    r = c.get(BASE + "/api/status/minecraft")
    if r.status_code == 200:
        d = r.json()
        ids = [s["id"] for s in d["servers"]]
        check("semantic", "status has 3 servers",
              set(ids) == {"survival", "creative", "network"}, str(ids))

    # ---- housekeeping: revoke the minted test session -------------------------
    conn = dbm.connect()
    tx = dbm.Database(conn)
    tx.run("UPDATE sessions SET revoked=1 WHERE token_hash=?",
           sec.token_digest(raw))
    tx.commit()
    conn.close()

    failed = [r for r in RESULTS if not r["ok"]]
    print(json.dumps({
        "ts": int(time.time()),
        "total": len(RESULTS),
        "failed": len(failed),
        "failures": failed,
    }, ensure_ascii=False, indent=1))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
