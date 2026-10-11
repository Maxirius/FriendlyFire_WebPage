# FriendlyFire_WebPage

Webpräsenz für den FriendlyFire Minecraft-Server (Fabric 26.3).

Enthält die öffentliche Willkommensseite, später Weltkarte (BlueMap) und
Statistiken. Gehostet auf dem Netcup-Root-Server, Inhalte werden lokal
gerendert und per rsync über WireGuard synchronisiert.

## Struktur

```
website/welcome/index.html   Willkommensseite (statisch, self-contained)
DEPLOYMENT_PLAN.md           Architektur & Deployment-Schritte
```

## Deployment (Kurzform)

1. Änderungen committen & pushen
2. `scp`/`rsync` von `website/welcome/index.html` nach
   `host.max-kuehn.com:/opt/hostings/minecraft_site/site/`
3. NPM-Route: `minecraft_site:8080` (siehe DEPLOYMENT_PLAN.md)

Später: `map/`-Unterordner als rsync-Ziel für BlueMap-Tiles.
