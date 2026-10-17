#!/usr/bin/env python3
"""Convert a Sponge .schem into a paced setblock RCON batch for the headless rig.

Usage: python3 schem_to_cmds.py SCHEMA.schem OFFSET_X OFFSET_Y OFFSET_Z OUT.cmds
Skips air and glass markers. Emits forceload first. Sign blocks get their
[TBM] front text baked in as SNBT.
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from parse_schem import load, unpack_varints  # noqa: E402


def main():
    path, ox, oy, oz, out = sys.argv[1], *(int(a) for a in sys.argv[2:5]), sys.argv[5]
    root = load(path)
    if "Schematic" in root:
        root = root["Schematic"]
    w, h, l = int(root["Width"]), int(root["Height"]), int(root["Length"])
    pal = {str(k): int(v) for k, v in root["Blocks"]["Palette"].items()}
    inv = {i: n for n, i in pal.items()}
    data = unpack_varints(bytes(root["Blocks"]["Data"]), w * h * l)

    cmds = [f"forceload add {ox - 8} {oz - 8} {ox + w + 8} {oz + l + 8}"]
    for y in range(h):
        for z in range(l):
            for x in range(w):
                name = inv[data[(y * l + z) * w + x]].strip('"')
                if ":air" in name or ":glass" in name:
                    continue
                wx, wy, wz = ox + x, oy + y, oz + z
                if name.split("[")[0].endswith("_sign"):
                    name = name + "{front_text:{messages:['[TBM]','','','']}}"
                cmds.append(f"setblock {wx} {wy} {wz} {name}")
    with open(out, "w") as f:
        f.write("\n".join(cmds) + "\n")
    print(f"wrote {len(cmds)} commands -> {out}")


if __name__ == "__main__":
    main()
