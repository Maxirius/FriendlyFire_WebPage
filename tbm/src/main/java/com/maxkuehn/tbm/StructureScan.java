package com.maxkuehn.tbm;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.ChestBlock;
import net.minecraft.world.level.block.entity.SignBlockEntity;
import net.minecraft.world.level.block.entity.SignTextSlot;
import net.minecraft.world.level.block.piston.PistonBaseBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.WallSignBlock;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Rule-based recognizer for player-built tunnel boring machines.
 *
 * Geometry contract (derived from Variante1-4.schem):
 *   sign (wall, line1 "[TBM]") -> iron mount -> furnace below mount
 *   mining dir = opposite of sign facing
 *   plane coordinates: along counted from the furnace plane (wall = 0,
 *   pistons = -1 toward rock, barrels = +1, rear pistons = +2, chests >= +1)
 */
final class StructureScan {

    private StructureScan() {
    }

    /** Outcome of a validation attempt. */
    record Result(TbmMachine machine, String errorKey, Object[] errorArgs) {

        static Result ok(TbmMachine m) {
            return new Result(m, null, null);
        }

        static Result fail(String key, Object... args) {
            return new Result(null, key, args);
        }

        boolean isSuccess() {
            return machine != null;
        }
    }

    /**
     * Validates the multiblock anchored at the clicked sign.
     */
    static Result validate(Level level, BlockPos signPos) {
        BlockState signState = level.getBlockState(signPos);

        // 1) must be a WALL sign (standing signs are ambiguous in yaw)
        if (!(signState.getBlock() instanceof WallSignBlock)) {
            return Result.fail("scan.sign-not-wall");
        }
        if (!(level.getBlockEntity(signPos) instanceof SignBlockEntity sign)) {
            return Result.fail("scan.sign-not-wall");
        }
        String line1 = sign.getText(SignTextSlot.FRONT).getMessages(false).getFirst().getString().trim();
        if (!"[TBM]".equals(line1)) {
            return Result.fail("scan.bad-label");
        }

        // 2) sign mounts on iron; furnace sits below the mount
        Direction signFacing = signState.getValue(WallSignBlock.FACING);
        BlockPos mount = signPos.relative(signFacing.getOpposite());
        if (!is(level, mount, Blocks.IRON_BLOCK)) {
            return Result.fail("scan.no-attach-iron");
        }
        BlockPos furnace = mount.below();
        if (!is(level, furnace, Blocks.FURNACE)) {
            return Result.fail("scan.no-furnace");
        }

        Direction mining = signFacing.getOpposite();
        Frame f = new Frame(furnace, mining);

        // NOTE on the frame convention: "along" counts STEPS ALONG the mining
        // direction (positive = toward the rock). Face pistons sit at along=+1
        // (one step in front of the wall plane), the backing wall at 0,
        // placer barrels at -1 and mirrored rear pistons at -2.

        // 3) front pistons: BFS in plane along = +1 seeded from the cell one
        //    step in front of the furnace
        BlockPos seed = f.cell(0, 0, 1);
        if (!isPiston(level, seed, mining)) {
            return Result.fail("scan.foreign-in-face", fmt(seed));
        }
        Set<BlockPos> pistons = bfsInPlane(level, seed, f, -1,
                p -> isPiston(level, p, mining));

        int cap = Math.max(1, TbmConfig.get().maxFaceDimension);
        Box faceBox = Box.around(f, 1, pistons);
        if (faceBox.w() > cap || faceBox.h() > cap) {
            return Result.fail("scan.face-too-large", cap, cap);
        }

        // every non-piston cell inside the face bbox must be air (clean
        // silhouette; rounded-face corners lie outside the bbox rim)
        for (BlockPos p : faceBox.cells(f)) {
            if (pistons.contains(p)) {
                continue;
            }
            if (!isEmptyOrReplaceable(level, p)) {
                return Result.fail("scan.foreign-in-face", fmt(p));
            }
        }

        // 4) iron backing wall: BFS in plane along = 0 seeded at the furnace
        //    (the furnace itself counts as a wall member so the seed matches)
        Set<BlockPos> wall = bfsInPlane(level, furnace, f, 0,
                p -> is(level, p, Blocks.IRON_BLOCK) || is(level, p, Blocks.FURNACE));
        for (BlockPos p : pistons) {
            BlockPos back = f.cell(u(p, f), v(p, f), 0);
            if (!wall.contains(back)) {
                return Result.fail("scan.piston-without-iron", fmt(p));
            }
        }

        // 5) placer ring (optional): mirrored rear piston at along=-2 with
        //    barrel at along=-1, same lateral cell; unpaired members are defects
        Set<BlockPos> barrels = new HashSet<>();
        Set<BlockPos> rearPistons = new HashSet<>();
        Box rearBox = faceBox.grow(1);
        for (BlockPos p : rearBox.cells(f, -2)) {
            boolean rp = isMirroredPiston(level, p, mining);
            BlockPos inFront = f.cell(u(p, f), v(p, f), -1);
            boolean bp = is(level, inFront, Blocks.BARREL);
            if (rp && bp) {
                rearPistons.add(p);
                barrels.add(inFront);
            } else if (rp) {
                return Result.fail("scan.orphan-barrel", fmt(inFront));
            } else if (bp) {
                return Result.fail("scan.orphan-barrel", fmt(p));
            }
        }

        // 6) chest census in the rear yard (informational)
        Set<BlockPos> chests = new HashSet<>();
        Box yard = faceBox.grow(1);
        for (int along = -1; along >= -8; along--) {
            for (BlockPos p : yard.cells(f, along)) {
                if (level.getBlockState(p).getBlock() instanceof ChestBlock) {
                    chests.add(p);
                }
            }
        }

        TbmMachine m = new TbmMachine();
        m.dimension = level.dimension().identifier().toString();
        m.anchor = TbmMachine.enc(furnace);
        m.miningDir = mining.getName();
        m.pistons.addAll(encAll(pistons));
        m.wall.addAll(encAll(wall));
        m.barrels.addAll(encAll(barrels));
        m.rearPistons.addAll(encAll(rearPistons));
        m.chests.addAll(encAll(chests));

        // 7) full hull: flood-fill over structural solids from everything
        //    found so far; unmatched iron becomes the "hull" remainder list
        //    (floors, side/top closures). Caps prevent runaway fills.
        Set<BlockPos> known = new HashSet<>();
        known.add(furnace);
        known.add(signPos);
        known.addAll(pistons);
        known.addAll(wall);
        known.addAll(barrels);
        known.addAll(rearPistons);
        known.addAll(chests);
        Set<BlockPos> hull = floodStructures(level, known);
        Set<BlockPos> classified = new HashSet<>(known);
        for (BlockPos p : hull) {
            if (!classified.contains(p) && is(level, p, Blocks.IRON_BLOCK)) {
                m.hull.add(TbmMachine.enc(p.immutable()));
            }
        }
        m.sign = TbmMachine.enc(signPos);

        return Result.ok(m);
    }

    // ---------------------------------------------------------------------

    private static List<String> encAll(Set<BlockPos> ps) {
        List<String> out = new ArrayList<>(ps.size());
        for (BlockPos p : ps) {
            out.add(TbmMachine.enc(p.immutable()));
        }
        return out;
    }

    private static boolean isPiston(Level level, BlockPos p, Direction mining) {
        BlockState s = level.getBlockState(p);
        return s.getBlock() == Blocks.PISTON
                && s.getValue(PistonBaseBlock.FACING) == mining
                && !s.getValue(PistonBaseBlock.EXTENDED);
    }

    private static boolean isMirroredPiston(Level level, BlockPos p, Direction mining) {
        BlockState s = level.getBlockState(p);
        return s.getBlock() == Blocks.PISTON
                && s.getValue(PistonBaseBlock.FACING) == mining.getOpposite()
                && !s.getValue(PistonBaseBlock.EXTENDED);
    }

    private static boolean isEmptyOrReplaceable(Level level, BlockPos p) {
        BlockState s = level.getBlockState(p);
        return s.isAir() || s.canBeReplaced() || s.is(BlockTags.REPLACEABLE);
    }

    private static boolean is(Level level, BlockPos p, net.minecraft.world.level.block.Block b) {
        return level.getBlockState(p).getBlock() == b;
    }

    private static String fmt(BlockPos p) {
        return p.getX() + "/" + p.getY() + "/" + p.getZ();
    }

    /** Lateral coordinate of p along the frame's u axis (signed steps). */
    private static int u(BlockPos p, Frame f) {
        return f.lateral(p, f.uDir);
    }

    private static int v(BlockPos p, Frame f) {
        return f.lateral(p, f.vDir);
    }

    /**
     * Flood-fill over ALL structural solids (iron, pistons in any state,
     * furnace, signs, barrels, chests) starting from the given seeds.
     * Returns the visited set including the seeds. Caps at 4000 cells.
     */
    private static Set<BlockPos> floodStructures(Level level, Set<BlockPos> seeds) {
        Set<BlockPos> seen = new HashSet<>(seeds);
        Deque<BlockPos> queue = new ArrayDeque<>(seeds);
        while (!queue.isEmpty()) {
            BlockPos cur = queue.poll();
            for (Direction d : Direction.values()) {
                BlockPos nxt = cur.relative(d);
                if (seen.add(nxt) && isStructural(level, nxt)) {
                    queue.add(nxt);
                } else if (!isStructural(level, nxt)) {
                    seen.remove(nxt);
                }
            }
            if (seen.size() > 4000) {
                break; // safety valve
            }
        }
        return seen;
    }

    private static boolean isStructural(Level level, BlockPos p) {
        BlockState s = level.getBlockState(p);
        return s.getBlock() == Blocks.IRON_BLOCK
                || s.getBlock() == Blocks.PISTON
                || s.getBlock() == Blocks.FURNACE
                || s.getBlock() instanceof net.minecraft.world.level.block.SignBlock
                || s.getBlock() == Blocks.BARREL
                || s.getBlock() instanceof ChestBlock;
    }

    /** Breadth-first flood over one plane (constant "along" coordinate). */
    private static Set<BlockPos> bfsInPlane(Level level, BlockPos seed, Frame f, int along,
                                            java.util.function.Predicate<BlockPos> match) {
        Set<BlockPos> seen = new HashSet<>();
        if (!match.test(seed)) {
            return seen;
        }
        Deque<BlockPos> queue = new ArrayDeque<>();
        seen.add(seed);
        queue.add(seed);
        while (!queue.isEmpty()) {
            BlockPos cur = queue.poll();
            for (Direction d : new Direction[]{f.uDir, f.vDir, f.uDir.getOpposite(), f.vDir.getOpposite()}) {
                BlockPos nxt = cur.relative(d);
                if (seen.add(nxt) && match.test(nxt)) {
                    queue.add(nxt);
                } else if (!match.test(nxt)) {
                    seen.remove(nxt);
                }
            }
            if (seen.size() > 4000) {
                break; // safety valve
            }
        }
        return seen;
    }

    /**
     * Orthogonal frame rooted at the furnace: "along" counts steps along the
     * mining axis (positive = rearward, negative = into the rock), u/v are
     * the two lateral axes with POSITIVE axis directions.
     */
    static final class Frame {
        final BlockPos origin;
        final Direction mining;
        final Direction uDir;
        final Direction vDir;

        Frame(BlockPos origin, Direction mining) {
            this.origin = origin.immutable();
            this.mining = mining;
            Direction.Axis ax = mining.getAxis();
            Direction.Axis ua = ax == Direction.Axis.X ? Direction.Axis.Z : Direction.Axis.X;
            Direction.Axis va = ax == Direction.Axis.Y ? Direction.Axis.Z : Direction.Axis.Y;
            this.uDir = Direction.fromAxisAndDirection(ua, Direction.AxisDirection.POSITIVE);
            this.vDir = Direction.fromAxisAndDirection(va, Direction.AxisDirection.POSITIVE);
        }

        /** Signed step count of p along the given lateral axis. */
        int lateral(BlockPos p, Direction d) {
            int pv = axisVal(p, d.getAxis());
            int ov = axisVal(origin, d.getAxis());
            return d.getAxisDirection() == Direction.AxisDirection.POSITIVE ? pv - ov : ov - pv;
        }

        int along(BlockPos p) {
            return lateral(p, mining);
        }

        private static int axisVal(BlockPos p, Direction.Axis ax) {
            return switch (ax) {
                case X -> p.getX();
                case Y -> p.getY();
                case Z -> p.getZ();
            };
        }

        /** Cell at (u, v, along) relative to the frame origin. */
        BlockPos cell(int u, int v, int along) {
            BlockPos p = origin;
            p = step(p, mining, along);
            p = step(p, uDir, u);
            p = step(p, vDir, v);
            return p;
        }

        private static BlockPos step(BlockPos p, Direction d, int n) {
            if (n == 0) {
                return p;
            }
            Direction dd = n > 0 ? d : d.getOpposite();
            BlockPos r = p;
            for (int i = 0; i < Math.abs(n); i++) {
                r = r.relative(dd);
            }
            return r;
        }
    }

    /** Rectangular bounds of a plane-resident point set in u/v coordinates. */
    static final class Box {
        final Frame f;
        final int along;
        int minU, maxU, minV, maxV;

        private Box(Frame f, int along) {
            this.f = f;
            this.along = along;
        }

        static Box around(Frame f, int along, Set<BlockPos> pts) {
            Box b = new Box(f, along);
            b.minU = b.minV = Integer.MAX_VALUE;
            b.maxU = b.maxV = Integer.MIN_VALUE;
            for (BlockPos p : pts) {
                b.minU = Math.min(b.minU, f.lateral(p, f.uDir));
                b.maxU = Math.max(b.maxU, f.lateral(p, f.uDir));
                b.minV = Math.min(b.minV, f.lateral(p, f.vDir));
                b.maxV = Math.max(b.maxV, f.lateral(p, f.vDir));
            }
            return b;
        }

        Box grow(int pad) {
            Box b = new Box(f, along);
            b.minU = minU - pad;
            b.maxU = maxU + pad;
            b.minV = minV - pad;
            b.maxV = maxV + pad;
            return b;
        }

        int w() {
            return maxU - minU + 1;
        }

        int h() {
            return maxV - minV + 1;
        }

        /** All cells of this box at its own along plane. */
        Iterable<BlockPos> cells(Frame frame) {
            return cells(frame, along);
        }

        /** All cells of this box projected onto the given along plane. */
        Iterable<BlockPos> cells(Frame frame, int targetAlong) {
            List<BlockPos> out = new ArrayList<>();
            for (int uu = minU; uu <= maxU; uu++) {
                for (int vv = minV; vv <= maxV; vv++) {
                    out.add(frame.cell(uu, vv, targetAlong));
                }
            }
            return out;
        }
    }
}
