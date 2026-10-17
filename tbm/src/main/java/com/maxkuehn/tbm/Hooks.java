package com.maxkuehn.tbm;

import net.fabricmc.fabric.api.event.player.AttackBlockCallback;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.entity.SignBlockEntity;
import net.minecraft.world.level.block.entity.SignTextSlot;
import net.minecraft.world.phys.BlockHitResult;

/**
 * Player interaction hooks: sign activation, machine block protection.
 */
final class Hooks {

    private Hooks() {
    }

    static void register() {
        // Right-click a [TBM] sign -> activate / status feedback.
        UseBlockCallback.EVENT.register(Hooks::onUseBlock);
        // Attacking a registered machine block is denied until deactivation.
        AttackBlockCallback.EVENT.register(Hooks::onAttackBlock);
    }

    private static InteractionResult onUseBlock(Player player, Level level, InteractionHand hand,
                                                BlockHitResult hit) {
        BlockPos pos = hit.getBlockPos();
        if (!(level.getBlockEntity(pos) instanceof SignBlockEntity sign)) {
            return InteractionResult.PASS;
        }
        String line1 = sign.getText(SignTextSlot.FRONT).getMessages(false).getFirst().getString().trim();
        if (!"[TBM]".equals(line1)) {
            return InteractionResult.PASS;
        }
        if (level instanceof ServerLevel serverLevel && player instanceof ServerPlayer sp) {
            Activation.activate(serverLevel, sp, pos);
            return InteractionResult.SUCCESS_SERVER;
        }
        return InteractionResult.PASS;
    }

    private static InteractionResult onAttackBlock(Player ply, Level level, InteractionHand hand,
                                                   BlockPos pos, net.minecraft.core.Direction direction) {
        TbmMachine m = TbmRegistry.get().machineAt(level.dimension(), pos);
        if (m != null) {
            if (ply instanceof ServerPlayer sp) {
                sp.sendSystemMessage(Messages.msg("protect.denied"));
            }
            return InteractionResult.FAIL;
        }
        return InteractionResult.PASS;
    }
}
