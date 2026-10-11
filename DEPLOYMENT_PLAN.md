# Deployment Plan: Minecraft Website on Netcup

Last updated: after initial deployment of the welcome page.

## Target picture
- Subdomain (e.g. map.max-kuehn.com, name TBD) → NPM → minecraft-site container
- Static content, fully hosted on the Netcup RS 1000 G11 (traffic never goes
  through the home upstream)
- Map: locally rendered tiles (BlueMap fabric mod on the MC server,
  AMP/DebianVM/Unraid)
  → rsync over the existing WireGuard tunnel (OPNsense ↔ Netcup)
  → /opt/hostings/minecraft_site/
- Statistics: JSON exports later, same sync path
- Login/player area: later phase

## Steps (completed)
1. ✅ Online snapshot taken (`pre-minecraft-site`)
2. ✅ New service added to /opt/hostings/docker-compose.yaml:
   ```yaml
   minecraft-site:
       image: nginxinc/nginx-unprivileged:latest
       container_name: minecraft_site
       restart: always
       volumes:
           - ./minecraft_site/site:/usr/share/nginx/html
       networks:
          - docker_network
       user: "${H_UID}:${H_GID}"
   ```
   Backup of the previous compose file: `docker-compose.yaml.bak_pre_mcsite`
3. 🔲 NPM route (to be created by the user manually) — forward to
   `minecraft_site:8080`
4. 🔲 Dedicated rsync/upload user still to discuss
5. 🔲 Check BlueMap compatibility with MC 26.3 (very fresh version!)
6. 🔲 Legacy `dynmap` container can eventually be removed (stopped since
   hardening anyway)

## Already deployed
- Welcome page (purely static, dark voxel theme):
  `/opt/hostings/minecraft_site/site/index.html`
  (source of truth: `website/welcome/index.html` in this repo)
- World-map section with automatic wake-up: probes `./map/maps/`;
  once BlueMap tile data exists there, the viewer loads automatically
