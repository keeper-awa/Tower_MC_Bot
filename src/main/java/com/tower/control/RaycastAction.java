package com.tower.control;

import com.google.gson.JsonObject;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.util.Optional;

/**
 * raycast（协议 §5.2）：准星射线扫描——从玩家眼睛沿视线方向射线，
 * 返回最近命中（方块或实体）或未命中。
 *
 * <p>方块射线走 {@code Entity#pick(double, float, boolean)}（1.20.1 第三参数为布尔：
 * true = 液体参与碰撞，false = 穿透液体——与 through_liquid 语义相反，取反传入）；
 * 实体射线为 boundingBox 手动裁剪（等价 ProjectileUtil 逻辑），两者取更近者。
 * face 语义：top/bottom/north/south/east/west（协议 §5.2）。
 */
public final class RaycastAction extends NativeAction {
    private static final int DISTANCE_MIN = 4;
    private static final int DISTANCE_MAX = 64;

    @Override
    protected JsonObject execute(LocalPlayer player, ActionContext ctx, JsonObject params)
            throws ActionException {
        int distance = optInt(params, "distance", 10, DISTANCE_MIN, DISTANCE_MAX);
        boolean throughLiquid = optBool(params, "through_liquid", false);

        Level level = player.level();
        Vec3 eye = player.getEyePosition(1.0F);
        Vec3 look = player.getLookAngle();
        Vec3 end = eye.add(look.scale(distance));

        // ── 方块射线：pick 第三参数 true=液体挡住射线（不穿过）──
        HitResult blockHit = player.pick(distance, 0.0F, !throughLiquid);
        double blockDist = blockHit.getType() == HitResult.Type.MISS
                ? Double.MAX_VALUE : eye.distanceTo(blockHit.getLocation());

        // ── 实体射线：沿线框手动裁剪 ──
        EntityHit entityHit = null;
        AABB searchBox = new AABB(eye, end).inflate(1.0);
        double best = blockDist;
        for (Entity e : level.getEntities(player, searchBox, e2 -> e2.isPickable() && !e2.isSpectator())) {
            Optional<Vec3> clip = e.getBoundingBox().inflate(e.getPickRadius()).clip(eye, end);
            if (clip.isPresent()) {
                double d = eye.distanceToSqr(clip.get());
                if (d < best * best) {
                    best = Math.sqrt(d);
                    entityHit = new EntityHit(e, best);
                }
            }
        }

        JsonObject hit = new JsonObject();
        if (entityHit != null && entityHit.distance < blockDist) {
            hit.addProperty("type", "entity");
            hit.addProperty("distance", entityHit.distance);
            hit.add("entity", entityJson(player, entityHit.entity));
        } else if (blockHit.getType() == HitResult.Type.BLOCK) {
            hit.addProperty("type", "block");
            hit.addProperty("distance", blockDist);
            hit.add("block", blockJson(level, (BlockHitResult) blockHit));
        } else {
            hit.addProperty("type", "none");
            hit.addProperty("distance", (double) distance);
        }

        JsonObject result = new JsonObject();
        result.add("hit", hit);
        return result;
    }

    private static JsonObject blockJson(Level level, BlockHitResult hit) {
        JsonObject b = new JsonObject();
        ResourceLocation id = level.registryAccess().registryOrThrow(Registries.BLOCK).getKey(
                level.getBlockState(hit.getBlockPos()).getBlock());
        b.addProperty("id", id == null ? "?" : id.toString());
        b.addProperty("x", hit.getBlockPos().getX());
        b.addProperty("y", hit.getBlockPos().getY());
        b.addProperty("z", hit.getBlockPos().getZ());
        b.addProperty("face", faceName(hit.getDirection()));
        return b;
    }

    /** 命中面语义（协议 §5.2）：top/bottom/north/south/east/west。 */
    private static String faceName(Direction dir) {
        switch (dir) {
            case UP:
                return "top";
            case DOWN:
                return "bottom";
            default:
                return dir.getName();
        }
    }

    private static JsonObject entityJson(LocalPlayer player, Entity e) {
        JsonObject o = new JsonObject();
        o.addProperty("id", e.getId());
        ResourceLocation type = player.level().registryAccess()
                .registryOrThrow(Registries.ENTITY_TYPE).getKey(e.getType());
        o.addProperty("type", type == null ? "?" : type.toString());
        o.addProperty("name", e.getName().getString());
        if (e instanceof LivingEntity living) {
            o.addProperty("health", living.getHealth());
        }
        return o;
    }

    /** 实体命中记录。 */
    private static final class EntityHit {
        final Entity entity;
        final double distance;

        EntityHit(Entity entity, double distance) {
            this.entity = entity;
            this.distance = distance;
        }
    }
}
