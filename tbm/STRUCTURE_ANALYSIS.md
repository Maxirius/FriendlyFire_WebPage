# TBM Structural Analysis (from Variante1-4.schem, MC 26.3, Sponge v3)

Parsed with `scripts/parse_schem.py`. Coordinates: X = width, Y = height, Z = depth;
machines advance toward **north (-Z)**; schematic-local coords used throughout.

## Overview

| Var | W×H×D | Face profile (front piston mask) | North pistons | South pistons | Barrels | Chests | Furnace | Sign |
|-----|--------|----------------------------------|----|----|----|----|---------|------|
| 1   | 5×4×7  | rectangle, TOP corners rounded   | 18 | 9  | 9  | 8 (4 pairs) | (2,1,1) | (2,2,2) |
| 2   | 7×6×8  | full rectangle                   | 42 | 22 | 22 | 12 (6 pairs)| (3,2,1) | (3,3,2) |
| 3   | 6×6×7  | octagon (circle-ish), pure drill | 24 | –  | –  | –  | (2,2,1) | (2,3,2) |
| 4   | 7×7×7  | octagon (circle-ish)             | 37 | 16 | 16 | 4 (2 pairs)| (3,3,1) | (3,4,2) |

All pistons in blueprints are `extended=false`. Front pistons: `facing=north`
(mining direction). Rear (placer) pistons: `facing=south` — mirrored, as specified.

## Shared anatomy (consistent across all variants)

Advancing axis: pistons occupy the smallest-Z planes; iron backing wall at z_face+1;
machine opens toward the rear (largest Z).

1. **Front face (z=0):** contiguous cluster of north-facing pistons. Profile is free-form
   (rectangle / rounded / octagonal observed). Corners of the face bbox may be air.
2. **Backing wall (z=1):** iron blocks fill the complement of the piston silhouette +
   close the ring diagonally (cells that are air at z=0 corners are iron at z=1).
   Contains exactly ONE furnace, `facing=south`, roughly centered in the wall.
3. **Shell:** iron closures extend backward along top/bottom/sides; interior hollow;
   rear end OPEN (air). Extent varies (deepest iron reaches z_max in V1 bottom rows).
4. **Floor:** iron slab inside the hull (lowest full row of the interior), carrying the
   chest battery in placer variants. Present even in pure-drill V3 (so: allowed generally).
5. **Energy furnace:** one furnace embedded in the backing wall, mouth facing the
   machine interior (south). Burns standard fuels.
6. **Sign:** oak WALL SIGN, front `facing=south` (reads toward the rear), line 1 exactly
   `[TBM]`. Mount block = the iron block DIRECTLY above the furnace
   (same X, z=z_furn+1... specifically: sign at (fx, fy+1, fz+1), mounted on (fx, fy+1, fz)).
   Activation gesture: right-click the sign.
7. **Placer kit (optional, distinguishes placer variants):**
   - **Barrel ring:** barrels lining the INNER side of the shell at the first interior
     plane (z≈2), all `facing=south` (openings toward rear). Counts scale with size.
   - **South piston clusters:** directly behind the barrel ring (z≈3), silhouettes
     mirror portions of the barrel ring.
   - **Chest battery:** paired double-chests (east/west halves) standing on the iron
     floor, rows stretching toward the rear.
8. **Glass:** 1-2 stray glass blocks = construction markers, IGNORED by validation.

## Derived validation grammar (proposal)

Seed = wall sign, front text line 1 == `[TBM]`.
Mount → iron block above furnace → furnace (unique) → derive axes:
advance dir = -(sign front facing). Then:

- R1: furnace exists, uniquely, in the backing wall plane; all 8 neighbors iron.
- R2: contiguous piston cluster in front of the wall, all facing advance-dir;
  face area within configured min/max bounds.
- R3: every silhouette-gap cell at z(face)+1 is iron (ring closes behind the face);
  the iron shell is contiguous (flood-fill over iron+pistons+furnace+barrels+chests+sign).
- R4: placer classification = presence of barrel ring (≥1 barrel) AND south-facing
  piston cluster behind it. Chests optional accessory (feeds barrels / receives loot).
- R5: sign attached to iron above furnace, facing anti-advance, `[TBM]`.
- Ignored blocks: air, water(?), glass.

Runtime concept (per user description): activation → validated → machine strokes
forward 1 block per cycle (whole structure displaces itself via its own pistons),
destroying blocks ahead of the piston heads (drops OFF by default, config-switchable,
optionally routed into rear chests); placer consumes 1-slot-barrel stock to line/support
the bore; furnace fuel = energy budget.

## Open questions (asked to user 2026-10-15)

- Chests mandatory for placer validity or purely logistical?
- Barrel constraint: force 1 slot / 1 item type per barrel via server-side menu locking?
- Stroke timing, fuel consumption per block, scaling with face area?
- Rotation: machine buildable facing any cardinal direction (validator rotates)?
- Exact semantic of rear south-pistons (lining the tunnel behind?)
