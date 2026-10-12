# Deployment Plan: Minecraft Website on Netcup

Last updated: after switching the welcome page to embed the live BlueMap.

## Actual architecture (as built)

Two routes live behind NPM on the shared `docker_network`:

| Domain | Target | Purpose |
|---|---|---|
| `mc.max-kuehn.com` | `minecraft_site:8080` | Welcome page (this repo, `website/welcome/index.html`) |
| `map.max-kuehn.com` | `bluemap_nginx:8080` | Live BlueMap web app |

BlueMap stack details:
- `bluemap_nginx` (nginx-unprivileged) serves the static web app from
  `/opt/hostings/minecraft_site/map_app` (custom conf in
  `minecraft_site/conf/default.conf`)
- `bluemap_php` (PHP-FPM 8.3, image `bluemap-php:fpm83`) implements the
  BlueMap data contract via `sql.php` — tiles live in MariaDB, not flat
  files. DB credentials in `bluemap_db_secret.env` (gitignored!)
- `sql.php` source is protected (returns 404 via nginx)
- Three worlds configured: `world`, `world_the_nether`, `world_the_end`

## Welcome page behaviour
- Entire site (incl. this page) sits behind the login wall served by
  `minecraft_site` nginx `auth_request` -> `minecraft_auth` `/gate`;
  anonymous visitors only ever see /login, /register, /forgot, /reset
  and /assets/*
- Map at map.max-kuehn.com REMAINS PUBLIC (explicit user decision)

## Completed milestones
- ✅ Online snapshot taken (`pre-minecraft-site`)
- ✅ `minecraft-site` service added to /opt/hostings/docker-compose.yaml
  (backup: `docker-compose.yaml.bak_pre_mcsite`)
- ✅ NPM routes for `mc.` and `map.max-kuehn.com`
- ✅ BlueMap rendering pipeline live (nginx + php-fpm + MariaDB storage)

## Remaining items
1. 🔲 ~~Login/player area~~ → IMPLEMENTED (see "Authentication architecture");
   awaiting deployment: build `friendlyfire-auth` image, insert compose
   services, create Mailcow mailbox for noreply@max-kuehn.com, fill
   `auth.env`, then flip the nginx config on minecraft_site
2. 🔲 Discord invite link in the nav button
3. 🔲 Final branding: server name / wordmark
4. 🔲 Statistics section (later phase)
5. 🔲 Legacy `dynmap` container can be removed (superseded by BlueMap)

## Change workflow
All changes happen in this repo, get committed/pushed, then deployed:

```
scp website/welcome/index.html hermes@host.max-kuehn.com:/tmp/mc_index.html
ssh hermes@host.max-kuehn.com 'sudo mv /tmp/mc_index.html /opt/hostings/minecraft_site/site/index.html && sudo chown hosting:hosting /opt/hostings/minecraft_site/site/index.html && sudo chmod 644 /opt/hostings/minecraft_site/site/index.html'
```

Snapshot before any structural server change (compose edits, new services).

## Auth-service deployment steps (when approved)
1. Snapshot gate (`pre-minecraft-auth` online snapshot).
2. Sync `server/` to `/opt/hostings/minecraft_auth/build/` (owner hosting).
3. Create Mailcow mailbox `noreply@max-kuehn.com`; put its password
   into `/opt/hostings/minecraft_auth/auth.env` (chmod 600, root-owned) —
   start from `server/auth.env.template`; set a strong FF_ADMIN_PASSWORD.
4. Regenerate docker-compose.yaml via python3+PyYAML inserting BOTH
   services from `server/compose-snippet.reference.yaml`
   (minecraft-auth build + minecraft-site conf bind-mount), keeping
   everything else byte-identical. Backup first: `.bak_pre_ffauth`.
5. `sudo docker compose config >/dev/null` then
   `sudo docker compose up -d minecraft-auth minecraft-site`.
6. Verify: `docker exec nginx_proxy_manager-app-1 curl -s -o /dev/null
   -w '%{http_code}' http://minecraft_auth:8000/health` -> 200; then
   anonymous browse of mc.max-kuehn.com must land on /login.
7. User creates/logs in with the bootstrap admin, rotates the password.
