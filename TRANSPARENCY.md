# Transparency notes (owner-authored)

Why this repo exists: anyone interested should be able to READ how the
community portal behaves — what it collects, whom it talks to, and where
potential abuse surface lies. It is not a deployment kit.

Everything below is claimed by the operator and SHOULD be cross-checked
against the code it references. Discrepancies are worth reporting.

## Components and where to look

| Concern                          | Entry point |
|----------------------------------|-------------|
| Page routing, login/logout, invites, password resets, admin views | `server/app.py` |
| Password/token hashing, session cookie mechanics | `server/security.py` |
| Database schema/access            | `server/db.py`, `server/mcstats_db.py` |
| Pulling gameplay statistics from the game VM | `server/stats_ingest.py` |
| Live server/player status probing | `server/mcstatus_service.py`, `server/presence.py`, `server/activity.py` |
| Avatar fetching                   | `server/mojang.py`, `server/avatar.py` |
| Outgoing mail                     | `server/mailer.py` |
| Latency/beacon estimator          | `server/door_beacon.py` |
| Whitelist-sync plugin (runs ON the Velocity proxy) | `server/velocity-plugin/` |

Unit tests mirroring the important flows: `server/tests/`.

## What visitors expose / what the site contacts externally

- Webfonts (three families, SIL OFL) are SELF-HOSTED under
  /assets/fonts/ — no third party learns anything about visitors
  from typography. (Previously a Google Fonts CDN was used; that
  disclosed visitor IPs to Google and was retired.)
- Avatars are resolved via Mojang session/texture servers.
- The latency test sends browser-initiated probes to dedicated beacon
  endpoints (`door_beacon.py` speaks TLS itself); it measures connection
  timing only and receives no gameplay data.
- Outgoing SMTP is used exclusively for transactional mail
  (registration/invite/password-reset), see `mailer.py`.

## Stored data

- Accounts: email, salted/hashed password, role flags.
- Sessions, invites, password-resets: only HASHED opaque tokens are
  persisted (look for `token_digest` usage in `security.py`/`app.py`);
  plaintext tokens live solely in the holder's cookie/mail.
- Gameplay statistics arrive via periodic pull from a WireGuard-reachable
  collector (`stats_ingest.py`) and are archived in SQLite.
- Environment files (`.env`) hold credentials and are never committed;
  `auth.env.template` documents the knobs.

## Deliberately excluded from this repository

- All Mojang-copyrighted graphics (icons, advancement artwork) — see
  README for the regeneration script.
- Any credential-bearing configuration.
- Infrastructure specifics beyond what the code implies.

## Sanitization performed for publication

Mechanical, whole-file substitutions applied to text files — nothing
was edited line-wise or hand-hidden:

| Published | Actual |
|-----------|--------|
| `example.com` (incl. `mc.`/`map.`/`ping.`/`host.` prefixes, `noreply@`) | `max-kuehn.com` family |
| `EXAMPLE` (uppercase wordmark) | `MAX-KUEHN` |
| `play.example.com` | `drck.dev` (gameserver door hostname) |
| `maxirius@users.noreply.github.com` | personal Gmail address |
| `Maxirius` | operator's legal name |
| `198.18.0.6` | `192.168.10.6` (RFC1918 address of the game VM behind WireGuard) |

Git history is intentionally a fresh start: the previous history embedded
personal contact data in every commit, so wiping it was the only way to
publish without leaking. Consequence: commit lineage does not reflect the
development timeline; treat the tip as the reviewed truth.
