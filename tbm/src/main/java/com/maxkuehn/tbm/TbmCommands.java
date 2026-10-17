package com.maxkuehn.tbm;

import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.commands.Commands;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.block.entity.SignBlockEntity;
import net.minecraft.world.phys.Vec3;

import java.util.List;

/**
 * {@code /tbm} administration commands (English root per convention).
 */
final class TbmCommands {

    private TbmCommands() {
    }

    static void register() {
        CommandRegistrationCallback.EVENT.register((dispatcher, ctx, env) -> {
            LiteralArgumentBuilder<net.minecraft.commands.CommandSourceStack> root =
                    Commands.literal("tbm");

            root.then(Commands.literal("list").executes(c -> {
                var source = c.getSource();
                List<TbmMachine> ms = TbmRegistry.get().all();
                if (ms.isEmpty()) {
                    source.sendSuccess(() -> Messages.msg("cmd.list.empty"), false);
                    return 1;
                }
                source.sendSuccess(() -> Messages.msg("cmd.list.header", ms.size()), false);
                for (TbmMachine m : ms) {
                    String status = m.pistons.isEmpty() ? Messages.raw("cmd.list.idle") : "";
                    source.sendSuccess(() -> Component.literal(Messages.raw("cmd.list.row",
                            "TBM@" + m.anchor, m.dimension, m.miningDir.toUpperCase(),
                            m.pistons.size(), status)), false);
                }
                return ms.size();
            }));

            root.then(Commands.literal("deactivate").executes(c -> {
                var source = c.getSource();
                if (!(source.getEntity() instanceof ServerPlayer player)) {
                    source.sendFailure(Messages.msg("cmd.players-only"));
                    return 0;
                }
                Vec3 eye = player.getEyePosition();
                Vec3 look = player.getViewVector(1.0F);
                for (double d = 0.5; d <= 8.0; d += 0.25) {
                    BlockPos probe = BlockPos.containing(eye.add(look.scale(d)));
                    for (TbmMachine m : TbmRegistry.get().near(player.level().dimension(), probe, 1.5)) {
                        boolean removed = TbmRegistry.get().remove(
                                player.level().dimension().identifier().toString(), m.anchorPos());
                        if (removed) {
                            player.sendSystemMessage(Messages.msg("cmd.deactivated"));
                            TbmMod.LOGGER.info("TBM deactivated by {}: anchor={}",
                                    player.getGameProfile().name(), m.anchor);
                            return 1;
                        }
                    }
                }
                player.sendSystemMessage(Messages.msg("cmd.deactivate.none"));
                return 0;
            }));

            // Console/admin/testing: activate a machine at explicit sign coords
            root.then(Commands.literal("activate")
                    .then(Commands.argument("x", com.mojang.brigadier.arguments.IntegerArgumentType.integer())
                            .then(Commands.argument("y", com.mojang.brigadier.arguments.IntegerArgumentType.integer())
                                    .then(Commands.argument("z", com.mojang.brigadier.arguments.IntegerArgumentType.integer())
                                            .executes(c -> {
                                                var source = c.getSource();
                                                if (!(source.getLevel() instanceof ServerLevel level)) {
                                                    return 0;
                                                }
                                                BlockPos signPos = new BlockPos(
                                                        com.mojang.brigadier.arguments.IntegerArgumentType.getInteger(c, "x"),
                                                        com.mojang.brigadier.arguments.IntegerArgumentType.getInteger(c, "y"),
                                                        com.mojang.brigadier.arguments.IntegerArgumentType.getInteger(c, "z"));
                                                if (!(level.getBlockEntity(signPos) instanceof SignBlockEntity)) {
                                                    source.sendFailure(Messages.msg("cmd.activate.not-a-sign"));
                                                    return 0;
                                                }
                                                Activation.activate(level, signPos,
                                                        comp -> source.sendSuccess(() -> comp, false));
                                                return 1;
                                            })))));

            dispatcher.register(root);
        });
    }
}
