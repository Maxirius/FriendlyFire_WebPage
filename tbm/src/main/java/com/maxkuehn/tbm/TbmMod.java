package com.maxkuehn.tbm;

import net.fabricmc.api.ModInitializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Tunnel Boring Machine — server-side multiblock mod. Entry point wires
 * config, messages, registry, interaction hooks and commands.
 */
public final class TbmMod implements ModInitializer {

    public static final Logger LOGGER = LoggerFactory.getLogger("tbm");

    @Override
    public void onInitialize() {
        TbmConfig.get();
        TbmRegistry.get().load();
        Hooks.register();
        TbmCommands.register();
        TbmEngine.register();
        LOGGER.info("TBM initialized (lang={}, collectDrops={})",
                TbmConfig.get().lang, TbmConfig.get().collectDrops);
    }
}
