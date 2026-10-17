package com.maxkuehn.tbm;

import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;

import java.util.function.Consumer;

/**
 * Sign right-click activation flow: validate the multiblock, register it,
 * and answer with success or the precise defect.
 */
final class Activation {

    private Activation() {
    }

    static void activate(ServerLevel level, ServerPlayer player, BlockPos signPos) {
        activate(level, signPos, player::sendSystemMessage);
    }

    /** Core flow with injectable responder (player chat or command feedback). */
    static void activate(ServerLevel level, BlockPos signPos, Consumer<Component> reply) {
        TbmMachine existing = TbmRegistry.get().machineAt(level.dimension(), signPos);
        if (existing != null) {
            reply.accept(Messages.msg("activate.already-active"));
            return;
        }
        StructureScan.Result result = StructureScan.validate(level, signPos);
        if (!result.isSuccess()) {
            reply.accept(Messages.msg(result.errorKey(), result.errorArgs()));
            return;
        }
        TbmMachine machine = result.machine();
        if (!TbmRegistry.get().register(machine)) {
            reply.accept(Messages.msg("activate.already-active"));
            return;
        }
        StringBuilder extra = new StringBuilder();
        if (!machine.barrels.isEmpty()) {
            extra.append(Messages.raw("activate.placer", machine.barrels.size()));
        }
        reply.accept(Messages.msg("activate.success", machine.memberCount(), extra.toString()));
        TbmMod.LOGGER.info("TBM activated: anchor={} dir={} members={} placerModules={} chests={}",
                machine.anchor, machine.miningDir, machine.memberCount(),
                machine.barrels.size(), machine.chests.size());
    }
}
