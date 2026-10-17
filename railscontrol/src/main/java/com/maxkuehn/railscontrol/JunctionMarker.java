package com.maxkuehn.railscontrol;

import net.minecraft.core.BlockPos;

/**
 * A manually declared junction/turnout ({@code /rails junction add}), looked at
 * while executing the command. Automatic detection during topology scanning
 * finds divergent cells on its own; manual declarations pin down spots the
 * scanner should treat as routable nodes even when they currently have a
 * single visible exit (e.g. temporarily broken or decorative switches).
 */
public final class JunctionMarker {
    public String dim;
    public int x, y, z;

    public JunctionMarker() {
    }

    public JunctionMarker(String dim, BlockPos pos) {
        this.dim = dim;
        this.x = pos.getX();
        this.y = pos.getY();
        this.z = pos.getZ();
    }

    public BlockPos pos() {
        return new BlockPos(x, y, z);
    }
}
