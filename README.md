# FriendlyFire Player Area — website + auth backend

Public source of the community portal: static frontend plus the FastAPI
authentication/account service behind it.

## Structure

```
website/   Static pages (HTML/CSS) + small helper scripts
           served by nginx; session-gated except /assets/.
server/    FastAPI auth service (sessions, invites, resets,
           stats ingestion) packaged as a Docker image.
```

## Regenerating the vanilla asset bundle

The Mojang-owned graphics under `website/assets/icons/` and the
advancement metadata in `website/assets/advancements.json` are **not**
part of this repository (third-party copyright). Recreate them locally
with the bundled extractor:

```bash
pip install pillow requests
python website/tools/extract_icons.py          # downloads the client jar
                                               # for the release version
```

It pulls the official client jar via the launcher manifest (SHA1-checked),
extracts item/block textures plus entity-face crops into
`website/assets/icons/`, and rebuilds `advancements.json` from the
misode/mcmeta summary branch (German translations resolved).

## Development

```bash
cd server
FF_ADMIN_EMAIL=admin@example.invalid FF_ADMIN_PASSWORD=change_me ./run_dev.sh
```

starts uvicorn on `:8787` serving `../website` statically. See
`server/auth.env.template` for configuration knobs.

All production-facing hostnames are replaced by `example.com` /
`play.example.com` placeholders throughout the source.
