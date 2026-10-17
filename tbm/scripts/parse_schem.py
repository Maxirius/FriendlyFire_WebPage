#!/usr/bin/env python3
"""Parse Sponge-format .schem files (v2/v3) and dump structure details.

Usage: python3 parse_schem.py FILE.schem [--layers]

Prints dimensions, palette histogram, per-Y-layer ASCII maps,
and block-entity payload (sign text, containers, states).
Coordinate mapping: x=scheme X, y=Y (vertical), z=Z. Index = (y*L + z)*W + x.
"""
import sys
import io
from collections import Counter

import nbtlib


def unpack_varints(raw: bytes, count: int):
    vals = []
    val = 0
    shift = 0
    for b in raw:
        val |= (b & 0x7F) << shift
        if b & 0x80:
            shift += 7
        else:
            vals.append(val)
            val = 0
            shift = 0
            if len(vals) == count:
                break
    return vals


def fmt_compound(c) -> dict:
    """Flatten an NBT CompoundTag into plain python."""
    out = {}
    for k, v in c.items():
        out[k] = _fmt_value(v)
    return out


def _fmt_value(v):
    import numpy as np
    if hasattr(v, "items") and not isinstance(v, (bytes, bytearray)):
        return {k: _fmt_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, nbtlib.List)):
        try:
            return [_fmt_value(x) for x in v]
        except TypeError:
            return repr(v)
    if isinstance(v, (np.ndarray,)):
        return v.tolist()
    if isinstance(v, (nbtlib.String, nbtlib.Int, nbtlib.Long, nbtlib.Short,
                      nbtlib.Byte, nbtlib.Float, nbtlib.Double)):
        return v.real if isinstance(v, float) else int(v) if not isinstance(v, nbtlib.String) else str(v)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return v


def load(path):
    f = nbtlib.load(path, gzipped=True)
    return f


def main():
    path = sys.argv[1]
    f = load(path)
    root = f
    if "Schematic" in root:          # sponge v3 wrapper
        root = root["Schematic"]

    def g(key, default=None):
        return root[key] if key in root else default

    width = int(g("Width")); height = int(g("Height")); length = int(g("Length"))
    print(f"FILE: {path}")
    print(f"DIMS: W(width/X)={width} H(height/Y)={height} L(length/Z)={length}")
    off = g("Offset")
    if off:
        print(f"OFFSET: {[int(o) for o in off]}")
    print(f"SpongeVersion: {g('Version')} DataVersion: {g('DataVersion')}")

    blocks_root = root
    if "Blocks" in root:             # sponge v3: palette/data nested
        blocks_root = root["Blocks"]

    palette_raw = blocks_root.get("Palette", g("Palette"))
    palette = {str(k): int(v) for k, v in palette_raw.items()}
    inverse = {idx: name for name, idx in palette.items()}
    n_blocks = width * height * length
    data = unpack_varints(bytes(blocks_root.get("Data", g("Data"))), n_blocks)

    hist = Counter(inverse[i] for i in data)
    print("\nPALETTE:")
    for name, cnt in sorted(hist.items()):
        print(f"  {cnt:>4}  {name}")

    # block entities
    bes = blocks_root.get("BlockEntities", g("BlockEntities", []) or [])
    print("\nBLOCK ENTITIES:")
    for be in bes:
        pos = be.get("Pos")
        if pos is not None:
            px, py, pz = (int(pos[0]), int(pos[1]), int(pos[2]))
        else:
            bp = be.get("BP"); x = be.get("x"); y = be.get("y"); z = be.get("z")
            if bp is not None:
                px, py, pz = int(bp[0]) >> 12, ((int(bp[0]) >> 8) & 0xFF) + (int(bp[1]) >> 8), int(bp[1]) & 0xFF
            elif x is not None:
                px, py, pz = int(x), int(y), int(z)
            else:
                px = py = pz = -1
        bid = be.get("Id", "?")
        print(f"\n  @({px},{py},{pz}) id={bid}")
        flat = fmt_compound(be)
        for k, v in flat.items():
            if k in ("Id", "keepPacked"):
                continue
            print(f"    {k}: {_truncate(v)}")

    # layer maps
    show_layers = "--layers" in sys.argv
    if show_layers or True:
        print("\nLAYERS (top-down; legend: first letter of block id):")
        legend = {}
        letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ123456789#@%&*+=~?"
        uniq = sorted(set(n.split(":")[1].split("[")[0] for n in palette.keys()))
        for i, blk in enumerate(uniq):
            legend[blk] = letters[i % len(letters)]
        print("LEGEND: " + "; ".join(f"{legend[b]}={b}" for b in uniq))
        for y in range(height - 1, -1, -1):
            print(f"-- Y={y} --")
            for z in range(length):
                row = ""
                for x in range(width):
                    name = inverse[data[(y * length + z) * width + x]]
                    base = name.split(":")[1].split("[")[0]
                    row += legend[base]
                print(row)


def _truncate(v, maxlen=400):
    s = repr(v)
    return s if len(s) <= maxlen else s[:maxlen] + "..."


unpack_variants = unpack_varints  # typo guard alias

if __name__ == "__main__":
    main()
