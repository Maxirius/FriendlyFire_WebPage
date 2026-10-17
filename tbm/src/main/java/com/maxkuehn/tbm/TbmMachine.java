package com.maxkuehn.tbm;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A recognized tunnel boring machine: immutable structure snapshot plus
 * mutable operational state. Positions are stored as compact "x|y|z"
 * strings so Gson serialization stays trivial.
 */
final class TbmMachine {

    /** Dimension identifier, e.g. "minecraft:overworld". */
    String dimension;

    /** Anchor = generator furnace position. */
    String anchor;

    /** Mining direction (where the pistons face). */
    String miningDir;

    /** Front pistons (one plane in front of the wall). */
    List<String> pistons = new ArrayList<>();

    /** Iron backing wall (includes the furnace's neighbors). */
    List<String> wall = new ArrayList<>();

    /** Placer barrels (paired 1:1 with rearPistons). */
    List<String> barrels = new ArrayList<>();

    /** Placer rear pistons (mirrored facing). */
    List<String> rearPistons = new ArrayList<>();

    /** Supply/loot chests in the rear yard. */
    List<String> chests = new ArrayList<>();

    /**
     * Remainder of the iron hull (floors, side/top closures) not covered by
     * the categorized lists - captured by the structural flood-fill.
     */
    List<String> hull = new ArrayList<>();

    /** The activating [TBM] wall sign (moves with the machine). */
    String sign;

    /** Remaining burn units (filled from the furnace fuel slot). */
    int energyPool;

    /** Scheduled tick for the next propulsion stroke (not persisted). */
    transient long nextStrokeAt;

    // ----- helpers -------------------------------------------------------

    static String enc(BlockPos pos) {
        return pos.getX() + "|" + pos.getY() + "|" + pos.getZ();
    }

    static BlockPos dec(String s) {
        String[] p = s.split("\\|");
        return new BlockPos(Integer.parseInt(p[0]), Integer.parseInt(p[1]), Integer.parseInt(p[2]));
    }

    static Direction dirDec(String s) {
        return Direction.byName(s.toLowerCase(java.util.Locale.ROOT));
    }

    BlockPos anchorPos() {
        return dec(anchor);
    }

    Direction miningDirection() {
        return dirDec(miningDir);
    }

    /** Every stored member position (categories + hull + sign + anchor), deduplicated. */
    List<BlockPos> allPositions() {
        // LinkedHashSet: the furnace lives in BOTH `wall` (the wall predicate
        // admits it) and as `anchor` - duplicates would double-process cells
        // during translation and wedge the stroke mid-flight.
        java.util.LinkedHashSet<BlockPos> uniq = new java.util.LinkedHashSet<>();
        uniq.add(dec(anchor));
        if (sign != null) {
            uniq.add(dec(sign));
        }
        for (List<String> lst : List.of(pistons, wall, barrels, rearPistons, chests, hull)) {
            for (String s : lst) {
                uniq.add(dec(s));
            }
        }
        return new ArrayList<>(uniq);
    }

    /** Translates every stored position by one block along the mining dir. */
    void shiftForward(Direction mining) {
        anchor = enc(dec(anchor).relative(mining));
        if (sign != null) {
            sign = enc(dec(sign).relative(mining));
        }
        for (List<String> lst : List.of(pistons, wall, barrels, rearPistons, chests, hull)) {
            lst.replaceAll(s -> enc(dec(s).relative(mining)));
        }
    }

    boolean contains(BlockPos pos) {
        String key = enc(pos);
        return pistons.contains(key) || wall.contains(key) || barrels.contains(key)
                || rearPistons.contains(key) || chests.contains(key) || hull.contains(key)
                || anchor.equals(key) || (sign != null && sign.equals(key));
    }

    int memberCount() {
        return allPositions().size();
    }

    /** JSON-safe view for persistence. */
    Map<String, Object> toJson() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("dimension", dimension);
        m.put("anchor", anchor);
        m.put("miningDir", miningDir);
        m.put("pistons", pistons);
        m.put("wall", wall);
        m.put("barrels", barrels);
        m.put("rearPistons", rearPistons);
        m.put("chests", chests);
        m.put("hull", hull);
        if (sign != null) {
            m.put("sign", sign);
        }
        m.put("energyPool", energyPool);
        return m;
    }

    @SuppressWarnings("unchecked")
    static TbmMachine fromJson(Map<String, Object> m) {
        TbmMachine t = new TbmMachine();
        t.dimension = String.valueOf(m.get("dimension"));
        t.anchor = String.valueOf(m.get("anchor"));
        t.miningDir = String.valueOf(m.get("miningDir"));
        for (String f : new String[]{"pistons", "wall", "barrels", "rearPistons", "chests", "hull"}) {
            Object v = m.get(f);
            if (v instanceof List<?> l) {
                for (Object o : l) {
                    switch (f) {
                        case "pistons" -> t.pistons.add(String.valueOf(o));
                        case "wall" -> t.wall.add(String.valueOf(o));
                        case "barrels" -> t.barrels.add(String.valueOf(o));
                        case "rearPistons" -> t.rearPistons.add(String.valueOf(o));
                        case "chests" -> t.chests.add(String.valueOf(o));
                        case "hull" -> t.hull.add(String.valueOf(o));
                        default -> throw new IllegalStateException("unknown field " + f);
                    }
                }
            }
        }
        t.sign = m.containsKey("sign") ? String.valueOf(m.get("sign")) : null;
        if (m.containsKey("energyPool")) {
            t.energyPool = ((Number) m.get("energyPool")).intValue();
        }
        return t;
    }
}
