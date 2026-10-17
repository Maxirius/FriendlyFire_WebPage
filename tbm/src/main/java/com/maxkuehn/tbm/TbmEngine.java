package com.maxkuehn.tbm;

import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.Containers;
import net.minecraft.world.Container;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.SignBlockEntity;
import net.minecraft.world.level.block.entity.SignText;
import net.minecraft.world.level.block.entity.SignTextSlot;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.Blocks;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Propulsion engine: drives every registered machine forward one block per
 * stroke interval, draining the furnace fuel pool, destroying the swept
 * face layer (no drops by default) and relocating the whole structure -
 * block states and container contents alike - with the machine.
 *
 * Movement model: every member slides one cell along the mining direction.
 * Members are relocated foremost-first so each destination cell is vacated
 * by the member in front before the follower arrives. The swept layer (the
 * plane directly in front of the face) is destroyed beforehand; fluids in
 * the way are displaced like blocks.
 */
final class TbmEngine {

    private TbmEngine() {
    }

    /** Burn units contributed by one fuel item (roughly vanilla furnace parity). */
    private static final Map<Item, Integer> FUEL_UNITS = buildFuelTable();

    /** Throttle for "out of fuel" log chatter (once per 10 s per machine). */
    private static final Map<TbmMachine, Long> LAST_FUEL_WARN = new HashMap<>();

    private static final long SAVE_EVERY_TICKS = 600;

    static void register() {
        ServerTickEvents.END_SERVER_TICK.register(TbmEngine::tick);
    }

    // ------------------------------------------------------------------
    // Scheduler
    // ------------------------------------------------------------------

    private static void tick(MinecraftServer server) {
        List<TbmMachine> scheduled = new ArrayList<>();
        long now = server.getTickCount();
        for (TbmMachine m : TbmRegistry.get().all()) {
            ServerLevel lvl = levelById(server, m.dimension);
            if (lvl == null || !chunksLoaded(lvl, m)) {
                continue; // dormant until the dimension/chunks are around
            }
            if (m.nextStrokeAt == 0) {
                m.nextStrokeAt = now + Math.max(1, TbmConfig.get().advanceIntervalTicks);
                continue;
            }
            if (now >= m.nextStrokeAt) {
                m.nextStrokeAt = now + Math.max(1, TbmConfig.get().advanceIntervalTicks);
                scheduled.add(m);
            }
        }
        for (TbmMachine m : scheduled) {
            stroke(server, levelById(server, m.dimension), m);
        }
        if (now % SAVE_EVERY_TICKS == 0) {
            TbmRegistry.get().saveIfDirty();
        }
    }

    private static ServerLevel levelById(MinecraftServer server, String dimId) {
        for (ServerLevel lvl : server.getAllLevels()) {
            if (lvl.dimension().identifier().toString().equals(dimId)) {
                return lvl;
            }
        }
        return null;
    }

    private static boolean chunksLoaded(ServerLevel lvl, TbmMachine m) {
        for (BlockPos p : m.allPositions()) {
            if (!lvl.isLoaded(p)) {
                return false;
            }
        }
        return true;
    }

    // ------------------------------------------------------------------
    // One stroke
    // ------------------------------------------------------------------

    private static void stroke(MinecraftServer server, ServerLevel lvl, TbmMachine m) {
        if (lvl == null) {
            return;
        }
        Direction mining = m.miningDirection();

        // 0) loaded-guard: every destination and sweep cell must reside in
        //    loaded chunks - otherwise setBlock silently no-ops and the
        //    registry would phantom-drive away from the world blocks.
        List<BlockPos> sweep = sweepLayer(lvl, m);
        for (BlockPos src : m.allPositions()) {
            BlockPos dest = src.relative(mining);
            if (!lvl.isLoaded(dest) || !lvl.isLoaded(src)) {
                return; // dormant at the frontier of loaded space
            }
        }
        for (BlockPos p : sweep) {
            if (!lvl.isLoaded(p)) {
                return;
            }
        }

        // 0b) integrity precheck: EVERY member must physically exist - a
        //    missing member means a previous stroke half-completed (desync);
        //    refusing to stroke prevents eating the rest of the machine.
        for (BlockPos p : m.allPositions()) {
            if (lvl.getBlockState(p).isAir()) {
                TbmMod.LOGGER.error("TBM @{} DESYNC: member {} missing in world - stroke refused", m.anchor, p.toShortString());
                return;
            }
        }

        // 1) fuel AVAILABILITY (dry check - drawing happens after the sweep
        //    so aborted strokes never burn fuel)
        if (!hasFuel(lvl, m)) {
            warnOutOfFuel(server, m);
            return; // polite stall
        }

        // 2) obstacle audit: every destination cell must be free or ours
        for (BlockPos src : m.allPositions()) {
            BlockPos dest = src.relative(mining);
            if (m.contains(dest)) {
                continue; // handed over by a fellow member mid-stroke
            }
            BlockState ds = lvl.getBlockState(dest);
            if (ds.isAir() || ds.canBeReplaced() || !ds.getFluidState().isEmpty()) {
                continue;
            }
            return; // immovable terrain (or a rival machine): stall
        }

        // 3) sweep: destroy the layer in front of the face (no drops by
        //    default; collectDrops routing is a later milestone)
        for (BlockPos p : sweep) {
            BlockState s = lvl.getBlockState(p);
            if (s.isAir() || s.canBeReplaced()) {
                continue;
            }
            if (s.getDestroySpeed(lvl, p) < 0) {
                TbmMod.LOGGER.warn("TBM @{} halted: indestructible block at {}", m.anchor, p.toShortString());
                return;
            }
            lvl.destroyBlock(p, false);
        }

        // 3b) fuel DRAW: the stroke is committed, now pay for it
        drawFuel(lvl, m);

        // 4) translate: foremost members first, contents carried over.
        //    The [TBM] sign is EXCLUDED from the sliding loop: wall signs
        //    die when their support block vacates (whatever the update
        //    flags do), and a sign is trivially recreatable. It is torn
        //    down first and rebuilt at its new spot after the move.
        SignSpec signSpec = null;
        if (m.sign != null) {
            signSpec = SignSpec.capture(lvl, TbmMachine.dec(m.sign));
            lvl.removeBlockEntity(TbmMachine.dec(m.sign));
            lvl.setBlock(TbmMachine.dec(m.sign), Blocks.AIR.defaultBlockState(), 2);
        }

        List<BlockPos> order = new ArrayList<>(m.allPositions());
        if (m.sign != null) {
            order.remove(TbmMachine.dec(m.sign));
        }
        order.sort(Comparator.comparingInt((BlockPos p) -> progOf(m, p)).reversed());
        for (BlockPos src : order) {
            BlockState st = lvl.getBlockState(src);
            BlockEntity be = lvl.getBlockEntity(src);
            ItemStack[] stash = stashContents(be);

            lvl.removeBlockEntity(src);
            // NOTE: clear WITHOUT neighbour updates (flag 2) so nothing
            // reacts mid-flight.
            if (!lvl.getBlockState(src).isAir()
                    && !lvl.setBlock(src, Blocks.AIR.defaultBlockState(), 2)) {
                TbmMod.LOGGER.warn("TBM stroke: clearing {} failed - aborting stroke", src.toShortString());
                return;
            }
            BlockPos dest = src.relative(mining);
            if (!lvl.setBlock(dest, st, 2)) {
                TbmMod.LOGGER.warn("TBM stroke: placing {} at {} failed - aborting stroke", st, dest.toShortString());
                return;
            }
            if (be != null) {
                BlockEntity newBe = lvl.getBlockEntity(dest);
                restoreContents(newBe, stash);
                if (newBe != null) {
                    newBe.setChanged();
                }
            }
        }

        if (signSpec != null) {
            signSpec.place(lvl, mining);
        }

        // 5) bookkeeping
        String oldAnchor = m.anchor;
        m.shiftForward(mining);
        TbmRegistry.get().save(); // small file, every stroke = truthful state
        TbmMod.LOGGER.info("TBM stroke: anchor {} -> {}", oldAnchor, m.anchor);
    }

    private static boolean otherMachineAt(TbmMachine self, BlockPos pos) {
        for (TbmMachine other : TbmRegistry.get().all()) {
            if (other != self && other.dimension.equals(self.dimension) && other.contains(pos)) {
                return true;
            }
        }
        return false;
    }

    /**
     * Progress of p along the MINING direction, relative to the anchor:
     * cells ahead of the anchor (toward the rock) are POSITIVE. The
     * foremost member therefore has the maximum progress.
     */
    private static int progOf(TbmMachine m, BlockPos p) {
        Direction mining = m.miningDirection();
        BlockPos anchor = m.anchorPos();
        int av = axisVal(anchor, mining.getAxis());
        int pv = axisVal(p, mining.getAxis());
        return mining.getAxisDirection() == Direction.AxisDirection.POSITIVE ? pv - av : av - pv;
    }

    private static int axisVal(BlockPos p, Direction.Axis ax) {
        return switch (ax) {
            case X -> p.getX();
            case Y -> p.getY();
            case Z -> p.getZ();
        };
    }

    /**
     * The destructive envelope: the plane one step in front of the foremost
     * members, sized to the machine's lateral extremes.
     */
    private static List<BlockPos> sweepLayer(ServerLevel lvl, TbmMachine m) {
        Direction mining = m.miningDirection();
        int maxProg = Integer.MIN_VALUE;
        for (BlockPos p : m.allPositions()) {
            maxProg = Math.max(maxProg, progOf(m, p));
        }
        int sweepProg = maxProg + 1;
        Direction u = lateralU(mining), v = lateralV(mining);
        int minU = Integer.MAX_VALUE, maxU = Integer.MIN_VALUE;
        int minV = Integer.MAX_VALUE, maxV = Integer.MIN_VALUE;
        for (BlockPos p : m.allPositions()) {
            minU = Math.min(minU, latOf(p, u));
            maxU = Math.max(maxU, latOf(p, u));
            minV = Math.min(minV, latOf(p, v));
            maxV = Math.max(maxV, latOf(p, v));
        }
        List<BlockPos> cells = new ArrayList<>();
        for (int uu = minU; uu <= maxU; uu++) {
            for (int vv = minV; vv <= maxV; vv++) {
                cells.add(offsetCombination(m.anchorPos(), mining, sweepProg, u, uu, v, vv));
            }
        }
        return cells;
    }

    private static Direction lateralU(Direction mining) {
        Direction.Axis ax = mining.getAxis();
        Direction.Axis ua = ax == Direction.Axis.X ? Direction.Axis.Z : Direction.Axis.X;
        return Direction.fromAxisAndDirection(ua, Direction.AxisDirection.POSITIVE);
    }

    private static Direction lateralV(Direction mining) {
        Direction.Axis ax = mining.getAxis();
        Direction.Axis va = ax == Direction.Axis.Y ? Direction.Axis.Z : Direction.Axis.Y;
        return Direction.fromAxisAndDirection(va, Direction.AxisDirection.POSITIVE);
    }

    private static int latOf(BlockPos p, Direction d) {
        return axisVal(p, d.getAxis());
    }

    private static BlockPos offsetCombination(BlockPos origin, Direction mining, int along,
                                              Direction u, int uc, Direction v, int vc) {
        BlockPos p = origin;
        p = bump(p, mining, along);
        p = bump(p, u, uc);
        p = bump(p, v, vc);
        return p;
    }

    private static BlockPos bump(BlockPos p, Direction d, int n) {
        if (n == 0) {
            return p;
        }
        Direction dd = n > 0 ? d : d.getOpposite();
        for (int i = 0; i < Math.abs(n); i++) {
            p = p.relative(dd);
        }
        return p;
    }

    // ------------------------------------------------------------------
    // Fuel
    // ------------------------------------------------------------------

    /** Dry check: pool covers the stroke, or the furnace offers fuel. */
    private static boolean hasFuel(ServerLevel lvl, TbmMachine m) {
        int need = Math.max(1, TbmConfig.get().fuelPerStroke);
        if (m.energyPool >= need) {
            return true;
        }
        BlockEntity be = lvl.getBlockEntity(m.anchorPos());
        if (be instanceof Container c && c.getContainerSize() > 1) {
            ItemStack fuel = c.getItem(1);
            return !fuel.isEmpty() && FUEL_UNITS.getOrDefault(fuel.getItem(), 0) > 0;
        }
        return false;
    }

    /** Consumes one stroke's worth of fuel (call only after committing). */
    private static void drawFuel(ServerLevel lvl, TbmMachine m) {
        int need = Math.max(1, TbmConfig.get().fuelPerStroke);
        if (m.energyPool >= need) {
            m.energyPool -= need;
            return;
        }
        BlockEntity be = lvl.getBlockEntity(m.anchorPos());
        if (be instanceof Container c && c.getContainerSize() > 1) {
            ItemStack fuel = c.getItem(1);
            int units = fuel.isEmpty() ? 0 : FUEL_UNITS.getOrDefault(fuel.getItem(), 0);
            if (units > 0) {
                ItemStack taken = c.removeItem(1, 1);
                m.energyPool += units;
                // lava buckets leave an empty bucket in the output slot
                if (taken.getItem() == Items.LAVA_BUCKET && c.getContainerSize() > 2) {
                    ItemStack out = c.getItem(2);
                    if (out.isEmpty()) {
                        c.setItem(2, new ItemStack(Items.BUCKET));
                    } else if (out.getItem() == Items.BUCKET
                            && out.getCount() < out.getMaxStackSize()) {
                        out.grow(1);
                    } else {
                        Containers.dropItemStack(lvl, m.anchorPos().getX() + 0.5,
                                m.anchorPos().getY() + 0.5, m.anchorPos().getZ() + 0.5,
                                new ItemStack(Items.BUCKET));
                    }
                }
                m.energyPool -= need;
                TbmRegistry.get().markDirty();
            }
        }
    }

    private static void warnOutOfFuel(MinecraftServer server, TbmMachine m) {
        long now = server.overworld().getGameTime();
        Long last = LAST_FUEL_WARN.get(m);
        if (last == null || now - last > 200) {
            LAST_FUEL_WARN.put(m, now);
            TbmMod.LOGGER.info("TBM @{} out of fuel - standing by", m.anchor);
        }
    }

    private static Map<Item, Integer> buildFuelTable() {
        Map<Item, Integer> t = new HashMap<>();
        t.put(Items.COAL, 1600);
        t.put(Items.CHARCOAL, 1600);
        t.put(Items.COAL_BLOCK, 16000);
        t.put(Items.LAVA_BUCKET, 20000);
        t.put(Items.BLAZE_ROD, 2400);
        t.put(Items.DRIED_KELP_BLOCK, 4000);
        t.put(Items.STICK, 100);
        t.put(Items.BAMBOO, 50);
        t.put(Items.CRAFTING_TABLE, 300);
        t.put(Items.BOOKSHELF, 300);
        t.put(Items.JUKEBOX, 300);
        t.put(Items.NOTE_BLOCK, 300);
        t.put(Items.SCAFFOLDING, 400);
        t.put(Items.BOW, 300);
        return t;
    }

    // ------------------------------------------------------------------
    // Content transfer
    // ------------------------------------------------------------------

    /** Copies every container slot (null when the BE is not a container). */
    private static ItemStack[] stashContents(BlockEntity be) {
        if (!(be instanceof Container c)) {
            return null;
        }
        ItemStack[] out = new ItemStack[c.getContainerSize()];
        for (int i = 0; i < c.getContainerSize(); i++) {
            ItemStack s = c.getItem(i);
            out[i] = s.isEmpty() ? ItemStack.EMPTY : s.copy();
        }
        return out;
    }

    private static void restoreContents(BlockEntity be, ItemStack[] stash) {
        if (stash == null || !(be instanceof Container c)) {
            return;
        }
        for (int i = 0; i < Math.min(stash.length, c.getContainerSize()); i++) {
            c.setItem(i, stash[i]);
        }
        if (be instanceof net.minecraft.world.level.block.entity.RandomizableContainerBlockEntity rce) {
            rce.setChanged();
        }
    }
}

/**
 * Snapshot of the [TBM] wall sign for tear-down/rebuild translation.
 * Wall signs cannot survive their support block vacating, so the sign
 * leaves the sliding loop and is recreated after the move.
 */
final class SignSpec {

    private final BlockPos pos;
    private final BlockState state;
    private final SignText front;
    private final SignText back;
    private final boolean waxed;

    private SignSpec(BlockPos pos, BlockState state, SignText front, SignText back, boolean waxed) {
        this.pos = pos;
        this.state = state;
        this.front = front;
        this.back = back;
        this.waxed = waxed;
    }

    static SignSpec capture(ServerLevel lvl, BlockPos pos) {
        BlockState st = lvl.getBlockState(pos);
        SignText front = null;
        SignText back = null;
        boolean waxed = false;
        if (lvl.getBlockEntity(pos) instanceof SignBlockEntity se) {
            front = se.getText(SignTextSlot.FRONT);
            back = se.getText(SignTextSlot.BACK);
            waxed = se.isWaxed();
        }
        return new SignSpec(pos.immutable(), st, front, back, waxed);
    }

    /** Rebuilds the sign one step along the mining direction. */
    void place(ServerLevel lvl, Direction mining) {
        BlockPos dest = pos.relative(mining);
        if (!lvl.setBlock(dest, state, 2)) {
            TbmMod.LOGGER.warn("TBM stroke: sign rebuild at {} failed", dest.toShortString());
            return;
        }
        if (lvl.getBlockEntity(dest) instanceof SignBlockEntity se && front != null) {
            se.setText(front, SignTextSlot.FRONT);
            if (back != null) {
                se.setText(back, SignTextSlot.BACK);
            }
            se.setWaxed(waxed);
            se.setChanged();
        }
    }
}
