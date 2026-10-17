package com.maxkuehn.tbm;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Mod configuration, loaded from {@code config/tbm.json}.
 */
final class TbmConfig {

    /** Message language: "de" or "en". */
    String lang = "de";

    /**
     * false: mined blocks are destroyed without yielding any items.
     * true: mined items are routed into the machine's chests.
     */
    boolean collectDrops = false;

    /** Ticks between one-block propulsion steps (20 = one block per second). */
    int advanceIntervalTicks = 20;

    /**
     * Burn units drained from the machine's energy pool per one-block
     * advance (fixed, independent of face area). Coal = 1600 units.
     */
    int fuelPerStroke = 16;

    /** Hard cap for the piston face bounding box (performance guard). */
    int maxFaceDimension = 12;

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    private static TbmConfig instance;

    static synchronized TbmConfig get() {
        if (instance == null) {
            instance = load();
        }
        return instance;
    }

    private static TbmConfig load() {
        TbmConfig cfg = new TbmConfig();
        Path path = path();
        if (Files.exists(path)) {
            try {
                TbmConfig loaded = GSON.fromJson(Files.readString(path, StandardCharsets.UTF_8), TbmConfig.class);
                if (loaded != null) {
                    cfg = loaded;
                }
            } catch (IOException e) {
                TbmMod.LOGGER.warn("Could not read {}: keeping defaults ({})", path, e.toString());
            }
        } else {
            save(cfg, path);
        }
        Messages.lang = cfg.lang;
        return cfg;
    }

    private static void save(TbmConfig cfg, Path path) {
        try {
            Files.createDirectories(path.getParent());
            Files.writeString(path, GSON.toJson(cfg), StandardCharsets.UTF_8);
        } catch (IOException e) {
            TbmMod.LOGGER.warn("Could not write {}: {}", path, e.toString());
        }
    }

    private static Path path() {
        return FabricLoader.getInstance().getConfigDir().resolve("tbm.json");
    }
}
