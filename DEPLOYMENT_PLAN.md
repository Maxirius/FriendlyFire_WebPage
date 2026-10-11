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
- Direct iframe embed of `https://map.max-kuehn.com/` in the world-map
  section, plus a fullscreen link
- Requires WebGL2 in the visitor's browser (standard BlueMap requirement)

## Completed milestones
- ✅ Online snapshot taken (`pre-minecraft-site`)
- ✅ `minecraft-site` service added to /opt/hostings/docker-compose.yaml
  (backup: `docker-compose.yaml.bak_pre_mcsite`)
- ✅ NPM routes for `mc.` and `map.max-kuehn.com`
- ✅ BlueMap rendering pipeline live (nginx + php-fpm + MariaDB storage)

## Remaining items
1. 🔲 Discord invite link in the nav button
2. 🔲 Final branding: server name / wordmark (currently placeholder
   `MC.MAX-KUEHN.COM`)
3. 🔲 Statistics section (later phase)
4. 🔲 Login/player area (later phase)
5. 🔲 Legacy `dynmap` container can be removed (superseded by BlueMap)

## Change workflow
All changes happen in this repo, get committed/pushed, then deployed:

```
scp website/welcome/index.html hermes@host.max-kuehn.com:/tmp/mc_index.html
ssh hermes@host.max-kuehn.com 'sudo mv /tmp/mc_index.html /opt/hostings/minecraft_site/site/index.html && sudo chown hosting:hosting /opt/hostings/minecraft_site/site/index.html && sudo chmod 644 /opt/hostings/minecraft_site/site/index.html'
```

Snapshot before any structural server change (compose edits, new services).
