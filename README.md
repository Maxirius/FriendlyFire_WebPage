# FriendlyFire_WebPage

Web presence for the FriendlyFire Minecraft server (Fabric 26.3).

Contains the public welcome page, later the world map (BlueMap) and
statistics. Hosted on the Netcup root server; content is rendered locally
and synced via rsync over WireGuard.

## Structure

```
website/welcome/index.html   Members landing page (behind login wall)
website/dashboard.html       Logged-in account area (invites, profile)
website/login.html           Login form
website/register.html        Invite-registration form
website/forgot.html          Password-reset request
website/reset.html           New-password form (token from mail)
website/assets/site.css      Shared styling for auth/account pages
website/conf/default.conf    minecraft_site nginx config (auth_request!)
server/                      FriendlyFire auth service (FastAPI + SQLite)
server/tests/                Integration tests (mocked Mojang/SMTP)
DEPLOYMENT_PLAN.md           Architecture & deployment steps
```

## Authentication architecture

- `minecraft_auth` container (FastAPI): opaque server-side sessions
  (SHA-256-hashed tokens in SQLite), Argon2id password hashing, CSRF via
  Origin/Referer allowlist + Fetch-Metadata fallback, rate limiting,
  invite codes (hashed at rest, 48 h TTL), password-reset tokens (30 min),
  Mojang nickname verification, skin-face avatar rendering, Mailcow SMTP.
- `minecraft_site` nginx enforces the wall itself via `auth_request` ->
  `minecraft_auth:8000/gate`: anonymous visitors get 302 -> `/login` for
  every page except `/login`, `/register`, `/forgot`, `/reset`, `/assets/`.
- Defense in depth: the FastAPI app ALSO rejects protected pages/API
  without a session, so bypassing nginx gains nothing.
- Whitelist automation: `GET /whitelist.txt` (admin-only) exports the
  verified player UUIDs for the Fabric server.
- Server status: `GET /api/status/minecraft` (members-only, 20 s
  server-side cache) probes Survival/Creative/the Velocity entrance via
  the WireGuard tunnel (`mcstatus_service.py`, pure stdlib). Targets and
  kinds configurable via `FF_MC_STATUS_TARGETS` (JSON array with
  `id`/`label`/`kind`(`world`|`network`)/`address`). Answers carrying an
  AMP-wrapper "Powered by AMP" MOTD are classified as `stopped`
  (instance paused), not online. The welcome page renders this as a
  status block (two world cards + a network bar).
- Ping test: the page times beacons with `performance.now()` (median of
  5 samples). `GET /api/ping/beacons` (members-only) returns the routes:
  `door` (primary player door — beacon hosted AT the game VM, published
  via OPNsense NAT; enabled by setting `FF_DOOR_BEACON_URL`), `rz`
  (detour via the Netcup RZ — beacon terminating on the RS), plus
  optional extras from `FF_PING_BEACONS` (JSON array
  `[{id,label,url}]`). Because the RZ beacon never traverses the tunnel,
  the page adds `estimate.measuredTunnelMs` — the collector's
  `tunnelTransitMs` (naked TCP connect RS -> VM via WireGuard, best of
  2) — to the RZ measurement. The network status card probes the SAME
  player door (`drck.dev:25565` default, `FF_MC_STATUS_TARGETS`).
  There is deliberately NO Cloudflare route anymore. `/ping` echoes
  CORS for trusted origins so cross-origin beacons work.

## Deployment (quick guide)

1. Commit & push changes
2. `scp`/`rsync` from `website/welcome/index.html` to
   `host.max-kuehn.com:/opt/hostings/minecraft_site/site/`
3. NPM route: `minecraft_site:8080` (see DEPLOYMENT_PLAN.md)

Later: `map/` subfolder serves as the rsync target for BlueMap tiles.
