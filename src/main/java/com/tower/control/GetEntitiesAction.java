package com.tower.control;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.tower.net.MessageCodec;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.MobCategory;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.AABB;

import java.util.List;

/**
 * get_entities（协议 §5.2）：附近实体列表——类型/位置/血量/名字，可过滤。
 *
 * <p>category 映射：原版 MobCategory → monster / creature / ambient / water / item /
 * player / other；hostile = (category == "monster")。超出 {@code max} 截断（1..64）。
 */
public final class GetEntitiesAction extends NativeAction {
    private static final int RADIUS_MIN = 1;
    private static final int RADIUS_MAX = 32;
    private static final int MAX_MIN = 1;
    private static final int MAX_MAX = 64;

    @Override
    protected JsonObject execute(LocalPlayer player, ActionContext ctx, JsonObject params)
            throws ActionException {
        int radius = optInt(params, "radius", 16, RADIUS_MIN, RADIUS_MAX);
        int max = optInt(params, "max", 64, MAX_MIN, MAX_MAX);
        String typeFilter = null;
        var typeEl = params.get("type");
        if (typeEl != null && !typeEl.isJsonNull()) {
            if (!typeEl.isJsonPrimitive() || !typeEl.getAsJsonPrimitive().isString()) {
                throw new ActionException(MessageCodec.ERR_INVALID_PARAM, "type 必须是字符串");
            }
            typeFilter = typeEl.getAsString();
        }

        Level level = player.level();
        AABB box = new AABB(player.blockPosition()).inflate(radius);
        // 过滤：放行可拾取实体 + 掉落物（ItemEntity.isPickable() 返回 false，须单独放行——
        // 否则 get_entities 永远看不到物品，技能拾取引导失效）
        List<Entity> found = level.getEntities(player, box,
                e -> (e.isPickable() || e instanceof ItemEntity)
                        && !e.isSpectator() && e.isAlive());
        String filter = typeFilter;
        found = found.stream()
                .filter(e -> filter == null || typeId(level, e).equals(filter))
                .limit(max)
                .toList();

        JsonArray entities = new JsonArray();
        for (Entity e : found) {
            JsonObject o = new JsonObject();
            o.addProperty("id", e.getId());
            o.addProperty("type", typeId(level, e));
            o.addProperty("name", e.getName().getString());
            o.addProperty("x", e.getX());
            o.addProperty("y", e.getY());
            o.addProperty("z", e.getZ());
            if (e instanceof LivingEntity living) {
                o.addProperty("health", living.getHealth());
            }
            String category = category(e);
            o.addProperty("category", category);
            o.addProperty("hostile", "monster".equals(category));
            entities.add(o);
        }

        JsonObject result = new JsonObject();
        result.add("entities", entities);
        result.addProperty("count", entities.size());
        return result;
    }

    private static String typeId(Level level, Entity e) {
        ResourceLocation id = level.registryAccess()
                .registryOrThrow(Registries.ENTITY_TYPE).getKey(e.getType());
        return id == null ? "?" : id.toString();
    }

    /** category 映射（协议 §5.2）：monster/creature/ambient/water/item/player/other。 */
    private static String category(Entity e) {
        if (e instanceof Player) {
            return "player";
        }
        MobCategory mc = e.getType().getCategory();
        if (mc == null) {
            return "other";
        }
        switch (mc) {
            case MONSTER:
                return "monster";
            case CREATURE:
                return "creature";
            case AMBIENT:
                return "ambient";
            case WATER_CREATURE:
            case WATER_AMBIENT:
            case UNDERGROUND_WATER_CREATURE:
            case AXOLOTLS:
                return "water";
            case MISC:
                return "item";
            default:
                return "other";
        }
    }
}
