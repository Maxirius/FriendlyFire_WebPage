# Deployment-Plan: Minecraft-Website auf Netcup

Stand: erste Diskussion, noch nichts deployed.

## Zielbild
- Subdomain (z.B. map.max-kuehn.com, Name TBD) → NPM → minecraft-site-Container
- Statische Inhalte, komplett gehostet auf Netcup RS 1000 G11 (Traffic geht nicht über heimischen Uplink)
- Karte: lokal gerenderte Tiles (BlueMap-Fabric-Mod auf dem MC-Server, AMP/DebianVM/Unraid)
  → rsync über bestehendes WireGuard (OPNsense ↔ Netcup) → /opt/hostings/minecraft_site/
- Statistiken: später JSON-Exporte, gleicher Sync-Weg
- Login/User-Bereich: spätere Phase

## Schritte (noch offen)
1. Online-Snapshot vom Netcup-Server (PFLICHT vor jeder Änderung)
2. Neuen Service in /opt/hostings/docker-compose.yaml ergänzen:
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
3. Route in NPM anlegen (der User macht das selbst)
4. rsync-Zielverzeichnis + evtl. dedizierter User für Uploads klären
5. BlueMap-Mod auf Kompatibilität mit MC 26.3 prüfen (Version sehr frisch!)
6. Alter `dynmap`-Container kann irgendwann entsorgt werden (gestoppt seit Hardening)

## Bereits erstellt
- Willkommensseite (rein statisch, dark voxel-theme):
  /opt/data/projects/Gameserver/Minecraft 2026/website/welcome/index.html
