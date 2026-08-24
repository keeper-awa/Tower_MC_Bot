package com.tower.state;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.tower.net.WsSessionHandler;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.effect.MobEffect;
import net.minecraft.world.entity.player.Abilities;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

import java.util.List;

/**
 * 完整状态快照构建（《Tower协议.md》§6 get_state 响应）：
 * 前置最小快照 + Tower 扩展（inventory 槽位 0-40 / xp / effects / biome）。
 *
 * <p>**必须在游戏主线程调用**（由 NativeAction 封送）；只读玩家状态，不做任何修改。
 * 未进世界（player == null）由调用方判定并返回 301。
 *
 * <p>槽位语义与前置 v2.1 {@code move_item}/{@code equip} 对齐：
 * 0-8 工具栏、9-35 背包、36-39 盔甲（脚/腿/胸/头）、40 副手。
 */
public final class PlayerState {
    private PlayerState() {
    }

    /** 构建完整快照。调用前须确认 {@code player != null}。 */
    public static JsonObject snapshot(LocalPlayer player) {
        JsonObject root = new JsonObject();
        root.addProperty("protocol", WsSessionHandler.PROTOCOL_VERSION);

        // ── player（与前置 v2 §6 完全一致）──
        JsonObject p = new JsonObject();
        p.add("position", vec3(player.position()));
        p.add("rotation", rotation(player));
        p.add("motion", vec3(player.getDeltaMovement()));
        p.addProperty("on_ground", player.onGround());
        p.addProperty("health", player.getHealth());
        p.addProperty("food", player.getFoodData().getFoodLevel());
        p.addProperty("saturation", player.getFoodData().getSaturationLevel());
        p.addProperty("dimension", player.level().dimension().location().toString());
        p.addProperty("gamemode", gamemode());
        p.addProperty("alive", player.isAlive());
        p.addProperty("selected_slot", player.getInventory().selected);
        p.add("abilities", abilities(player.getAbilities()));
        root.add("player", p);

        // ── inventory（协议 §6：非空槽位 0-40）──
        Registry<Item> itemRegistry = player.level().registryAccess().registryOrThrow(Registries.ITEM);
        JsonObject inv = new JsonObject();
        inv.add("slots", slotList(player.getInventory().items, 0, itemRegistry));
        inv.add("armor", slotList(player.getInventory().armor, 36, itemRegistry));
        ItemStack offhand = player.getInventory().offhand.get(0);
        if (!offhand.isEmpty()) {
            inv.add("offhand", item(40, offhand, itemRegistry));
        }
        ItemStack held = player.getInventory().getSelected();
        if (!held.isEmpty()) {
            inv.add("held", item(-1, held, itemRegistry));
        }
        root.add("inventory", inv);

        // ── xp / effects / biome ──
        JsonObject xp = new JsonObject();
        xp.addProperty("level", player.experienceLevel);
        xp.addProperty("progress", player.experienceProgress);
        root.add("xp", xp);

        Registry<MobEffect> effectRegistry = player.level().registryAccess().registryOrThrow(Registries.MOB_EFFECT);
        JsonArray effects = new JsonArray();
        for (MobEffectInstance inst : player.getActiveEffects()) {
            JsonObject e = new JsonObject();
            ResourceLocation id = effectRegistry.getKey(inst.getEffect());
            e.addProperty("id", id == null ? "?" : id.toString());
            e.addProperty("amplifier", inst.getAmplifier());
            e.addProperty("duration", inst.getDuration());
            effects.add(e);
        }
        if (!effects.isEmpty()) {
            root.add("effects", effects);
        }

        JsonObject world = new JsonObject();
        world.addProperty("time_of_day", player.level().getDayTime());
        ResourceLocation biomeId = player.level().registryAccess()
                .registryOrThrow(Registries.BIOME).getKey(player.level().getBiome(player.blockPosition()).value());
        world.addProperty("biome", biomeId == null ? "?" : biomeId.toString());
        root.add("world", world);
        return root;
    }

    /** 非空槽位列表；start 为槽位偏移（items=0 / armor=36）。 */
    private static JsonArray slotList(List<ItemStack> stacks, int start, Registry<Item> itemRegistry) {
        JsonArray arr = new JsonArray();
        for (int i = 0; i < stacks.size(); i++) {
            ItemStack stack = stacks.get(i);
            if (!stack.isEmpty()) {
                arr.add(item(start + i, stack, itemRegistry));
            }
        }
        return arr;
    }

    /** 单个槽位条目 {@code {slot, id, count}}（Registry 由调用方传入，避免重复取）。 */
    private static JsonObject item(int slot, ItemStack stack, Registry<Item> itemRegistry) {
        JsonObject o = new JsonObject();
        if (slot >= 0) {
            o.addProperty("slot", slot);
        }
        ResourceLocation id = itemRegistry.getKey(stack.getItem());
        o.addProperty("id", id == null ? "?" : id.toString());
        o.addProperty("count", stack.getCount());
        return o;
    }

    private static JsonObject vec3(Vec3 v) {
        JsonObject o = new JsonObject();
        o.addProperty("x", v.x);
        o.addProperty("y", v.y);
        o.addProperty("z", v.z);
        return o;
    }

    private static JsonObject rotation(LocalPlayer player) {
        JsonObject o = new JsonObject();
        o.addProperty("yaw", player.getYRot());
        o.addProperty("pitch", player.getXRot());
        return o;
    }

    /** 游戏模式：survival / creative / adventure / spectator（协议 §6）。 */
    private static String gamemode() {
        var gameMode = net.minecraft.client.Minecraft.getInstance().gameMode;
        return gameMode == null ? "unknown" : gameMode.getPlayerMode().getName();
    }

    private static JsonObject abilities(Abilities ab) {
        JsonObject o = new JsonObject();
        o.addProperty("flying", ab.flying);
        o.addProperty("fly_allowed", ab.mayfly);
        return o;
    }
}
