# FriendlyFire_WebPage

Web presence for the FriendlyFire Minecraft server (Fabric 26.3).

Contains the public welcome page, later the world map (BlueMap) and
statistics. Hosted on the Netcup root server; content is rendered locally
and synced via rsync over WireGuard.

## Structure

```
website/welcome/index.html   Welcome page (static, self-contained)
DEPLOYMENT_PLAN.md           Architecture & deployment steps
```

## Deployment (quick guide)

1. Commit & push changes
2. `scp`/`rsync` from `website/welcome/index.html` to
   `host.max-kuehn.com:/opt/hostings/minecraft_site/site/`
3. NPM route: `minecraft_site:8080` (see DEPLOYMENT_PLAN.md)

Later: `map/` subfolder serves as the rsync target for BlueMap tiles.
