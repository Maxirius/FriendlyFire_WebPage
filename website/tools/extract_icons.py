#!/usr/bin/env python3
"""Re-extract the vanilla asset bundle for the website.

Generates everything that ships OUTSIDE of this repository because it is
copyrighted Mojang content:

  website/assets/icons/*.png          (~1250 item/block/head sprites)
  website/assets/advancements.json    (compact advancement metadata, de_DE)

Usage:
    pip install pillow requests
    python tools/extract_icons.py [--version 26.3]

Sources:
  - Official launcher meta (manifest -> version JSON -> signed client jar,
    SHA1-verified before extraction).
  - misode/mcmeta `summary` branch for advancement definitions + de_DE
    translations (definitions are no longer shipped in modern client jars).
"""

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

LAUNCHER_META = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
MCMETA_RAW = "https://raw.githubusercontent.com/misode/mcmeta/{branch}/{path}"

ROOT = Path(__file__).resolve().parent.parent          # website/
ASSETS = ROOT / "assets"
ICONS = ASSETS / "icons"

# Animated items: keep exactly one representative frame.
ANIMATED_KEEP_FRAME = {
    "clock": "clock_16",
    "compass": "compass_16",
}

# Non-item icons drawn from BLOCK textures instead (kept minimal, mirrors
# what the frontend registers in its CARD_ICONS / badge maps).
BLOCK_EXTRAS = [
    "iron_ore",
    "ancient_debris_side",
    "oak_log",
    "sculk",
    "dragon_egg",
    "red_bed_head_up",
]

# Head-like icons cropped from entity skins (item sprites do not exist).
HEAD_ENTITIES = [
    "zombie", "zombie_villager", "creeper", "skeleton", "wither_skeleton",
]


def _get_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "ff-site-asset-extractor"})) as r:
        return json.load(io.TextIOWrapper(r, encoding="utf-8"))


def _get_bytes(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "ff-site-asset-extractor"})) as r:
        return r.read()


def resolve_client_jar(version_arg: str | None) -> tuple[str, bytes]:
    """Return (version_id, client-jar bytes) with SHA1 verification."""
    manifest = _get_json(LAUNCHER_META)
    if version_arg:
        candidates = [v for v in manifest["versions"]
                      if v["id"].startswith(version_arg)]
        if not candidates:
            sys.exit(f"No launcher version starting with {version_arg!r}")
        vid_meta = candidates[0]
    else:
        rid = manifest["latest"]["release"]
        vid_meta = next(v for v in manifest["versions"] if v["id"] == rid)

    vinfo = _get_json(vid_meta["url"])
    dl = vinfo["downloads"]["client"]
    blob = _get_bytes(dl["url"])

    import hashlib
    actual = hashlib.sha1(blob).hexdigest()
    expected = dl["sha1"]
    if actual != expected:
        sys.exit(f"SHA1 mismatch: {actual} != {expected}")
    return vid_meta["id"], blob


def extract_icons(jar_blob: bytes, extra_blocks: list[str]) -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    zf = zipfile.ZipFile(io.BytesIO(jar_blob))
    names = set(zf.namelist())

    taken = set()

    def emit(src_name: str, out_name: str, data: bytes) -> None:
        if out_name.endswith(".png"):
            (ICONS / out_name).write_bytes(data)
            taken.add(out_name[:-4])

    for name in zf.namelist():
        if not name.startswith("assets/minecraft/textures/item/") \
                or not name.endswith(".png"):
            continue
        stem = Path(name).stem
        if "_" in stem and stem.rsplit("_", 1)[-1].isdigit():   # anim frame
            base = stem.rsplit("_", 1)[0]
            if ANIMATED_KEEP_FRAME.get(base) == stem:
                emit(name, f"{base}.png", zf.read(name))
            continue
        emit(name, f"{stem}.png", zf.read(name))

    for blk in [*BLOCK_EXTRAS, *extra_blocks]:
        zpath = f"assets/minecraft/textures/block/{blk}.png"
        if zpath in names:
            emit(zpath, f"{blk}.png", zf.read(zpath))

    # Crop 16x16 faces from entity skins (nearest-neighbour upscale-free:
    # the source rect is already 8x8 px -> scale x2 to reach 16x16).
    try:
        from PIL import Image
    except ImportError:
        print("pillow missing: skipping head crops")
    else:
        for ent in HEAD_ENTITIES:
            zpath = f"assets/minecraft/textures/entity/{ent}/{ent}.png"
            if zpath not in zf.namelist():
                continue
            img = Image.open(io.BytesIO(zf.read(zpath)))
            face = img.crop((8, 8, 16, 16)).resize((16, 16),
                                                   Image.NEAREST)
            buf = io.BytesIO()
            face.save(buf, "PNG")
            emit(zpath, f"{ent}_face.png", buf.getvalue())

    print(f"icons emitted: {len(taken)}")


def build_advancements() -> tuple[dict, list[str]]:
    defs = _get_json(MCMETA_RAW.format(branch="summary",
                                       path="data/advancement/data.json"))
    langs = _get_json(MCMETA_RAW.format(branch="summary",
                                        path="assets/lang/data.json"))
    de = langs.get("de_de", {})

    out = {}
    pairs = (defs.items() if isinstance(defs, dict)
             else ((a.get("id", i), a) for i, a in enumerate(defs)))
    for key, adv in pairs:
        disp = adv.get("display")
        if not disp:
            continue
        tid = disp["title"]["translate"]
        ic = disp.get("icon", {})
        iid = ic.get("item") or ic.get("id") or ""
        if not iid:
            continue
        out[key.replace("minecraft:", "")] = {
            "t": de.get(tid, tid.split(".")[-1]),
            "d": de.get(disp["description"]["translate"],
                        disp["description"]["translate"]),
            "i": iid.replace("minecraft:", ""),
        }

    return out, sorted({v["i"] for v in out.values()})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", help="launcher version prefix, e.g. 26.3")
    args = ap.parse_args()

    vid, blob = resolve_client_jar(args.version)
    print(f"using client jar for {vid}")

    adv, needed_icons = build_advancements()
    extract_icons(blob, needed_icons)

    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "advancements.json").write_text(
        json.dumps(adv, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"advancements emitted: {len(adv)}")


if __name__ == "__main__":
    main()
