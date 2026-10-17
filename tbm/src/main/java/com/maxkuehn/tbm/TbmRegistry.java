package com.maxkuehn.tbm;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.reflect.TypeToken;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.core.BlockPos;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.level.Level;

import java.io.IOException;
import java.lang.reflect.Type;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/**
 * Persistent registry of active TBM machines, stored at
 * {@code <world>/tbm/tbms.json}.
 */
final class TbmRegistry {

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
    private static final Type LIST_TYPE = TypeToken.getParameterized(List.class, Map.class).getType();

    private final List<TbmMachine> machines = new ArrayList<>();

    private volatile boolean dirty = false;

    // ------------------------------------------------------------------

    private static Path path() {
        return FabricLoader.getInstance().getGameDir().resolve("tbm").resolve("tbms.json");
    }

    private static final TbmRegistry INSTANCE = new TbmRegistry();

    static TbmRegistry get() {
        return INSTANCE;
    }

    void load() {
        machines.clear();
        Path p = path();
        if (!Files.exists(p)) {
            return;
        }
        try {
            List<Map<String, Object>> raw = GSON.fromJson(Files.readString(p, StandardCharsets.UTF_8), LIST_TYPE);
            if (raw != null) {
                for (Map<String, Object> m : raw) {
                    machines.add(TbmMachine.fromJson(m));
                }
            }
        } catch (IOException | RuntimeException e) {
            TbmMod.LOGGER.error("Failed to load {}: {}", p, e.toString());
        }
        TbmMod.LOGGER.info("TBM registry: {} machine(s) loaded", machines.size());
    }

    void save() {
        Path p = path();
        try {
            Files.createDirectories(p.getParent());
            List<Map<String, Object>> raw = new ArrayList<>();
            for (TbmMachine m : machines) {
                raw.add(m.toJson());
            }
            Files.writeString(p, GSON.toJson(raw), StandardCharsets.UTF_8);
            dirty = false;
        } catch (IOException e) {
            TbmMod.LOGGER.error("Failed to save {}: {}", p, e.toString());
        }
    }

    /** Flags the registry for the next periodic autosave. */
    void markDirty() {
        dirty = true;
    }

    /** Persists when flagged (called periodically from the engine). */
    void saveIfDirty() {
        if (dirty) {
            save();
        }
    }

    // ------------------------------------------------------------------

    /** Registers a machine; returns false if the anchor is already taken. */
    boolean register(TbmMachine m) {
        if (findByAnchor(m.dimension, TbmMachine.dec(m.anchor)) != null) {
            return false;
        }
        machines.add(m);
        save();
        return true;
    }

    /** Removes the machine identified by dimension + anchor, if present. */
    boolean remove(String dimension, BlockPos anchor) {
        Iterator<TbmMachine> it = machines.iterator();
        while (it.hasNext()) {
            TbmMachine m = it.next();
            if (m.dimension.equals(dimension) && TbmMachine.dec(m.anchor).equals(anchor)) {
                it.remove();
                save();
                return true;
            }
        }
        return false;
    }

    /** The machine holding the given block, or null. */
    TbmMachine machineAt(ResourceKey<Level> dim, BlockPos pos) {
        String dimId = dim.identifier().toString();
        for (TbmMachine m : machines) {
            if (!m.dimension.equals(dimId)) {
                continue;
            }
            if (m.contains(pos)) {
                return m;
            }
        }
        return null;
    }

    TbmMachine findByAnchor(String dimension, BlockPos anchor) {
        String key = TbmMachine.enc(anchor);
        for (TbmMachine m : machines) {
            if (m.dimension.equals(dimension) && m.anchor.equals(key)) {
                return m;
            }
        }
        return null;
    }

    List<TbmMachine> all() {
        return List.copyOf(machines);
    }

    /** Machines whose member blocks lie within the given radius of pos. */
    List<TbmMachine> near(ResourceKey<Level> dim, BlockPos pos, double radius) {
        String dimId = dim.identifier().toString();
        List<TbmMachine> out = new ArrayList<>();
        double r2 = radius * radius;
        for (TbmMachine m : machines) {
            if (!m.dimension.equals(dimId)) {
                continue;
            }
            for (BlockPos p : iterateMembers(m)) {
                if (p.distSqr(pos) <= r2) {
                    out.add(m);
                    break;
                }
            }
        }
        return out;
    }

    private static Iterable<BlockPos> iterateMembers(TbmMachine m) {
        List<BlockPos> out = new ArrayList<>();
        out.add(TbmMachine.dec(m.anchor));
        for (String s : m.pistons) {
            out.add(TbmMachine.dec(s));
        }
        for (String s : m.wall) {
            out.add(TbmMachine.dec(s));
        }
        for (String s : m.barrels) {
            out.add(TbmMachine.dec(s));
        }
        for (String s : m.rearPistons) {
            out.add(TbmMachine.dec(s));
        }
        for (String s : m.chests) {
            out.add(TbmMachine.dec(s));
        }
        for (String s : m.hull) {
            out.add(TbmMachine.dec(s));
        }
        if (m.sign != null) {
            out.add(TbmMachine.dec(m.sign));
        }
        return out;
    }

    private TbmRegistry() {
    }
}
