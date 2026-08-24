package com.tower.control;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * get_blocks（协议 §5.2）：玩家周围非空气方块列表（空气是默认值，不列，省消息空间）。
 *
 * <p>半径默认 8（立方体），超 {@code max} 截断（truncated=true，防超 64KB）；
 * <b>截断前按到玩家距离排序</b>——保证返回的总是最近的 {@code max} 个方块
 * （修复：扫描顺序截断在密集环境会漏掉近处方块，如沼泽/森林里的原木）。
 * summary 附脚下/面前（视线方向 1 格）/头上三格关键方块，供快速决策。
 */
public final class GetBlocksAction extends NativeAction {
    private static final int RADIUS_MIN = 1;
    private static final int RADIUS_MAX = 16;
    private static final int MAX_MIN = 1;
    private static final int MAX_MAX = 512;

    @Override
    protected JsonObject execute(LocalPlayer player, ActionContext ctx, JsonObject params)
            throws ActionException {
        int radius = optInt(params, "radius", 8, RADIUS_MIN, RADIUS_MAX);
        int max = optInt(params, "max", 512, MAX_MIN, MAX_MAX);

        Level level = player.level();
        BlockPos center = player.blockPosition();
        List<BlockPos> hits = new ArrayList<>();
        for (int dx = -radius; dx <= radius; dx++) {
            for (int dy = -radius; dy <= radius; dy++) {
                for (int dz = -radius; dz <= radius; dz++) {
                    BlockPos pos = center.offset(dx, dy, dz);
                    if (!level.getBlockState(pos).isAir()) {
                        hits.add(pos);
                    }
                }
            }
        }
        // 按到玩家距离排序（曼哈顿距离立方体扫描，欧氏距离一致），取最近的 max 个
        hits.sort(Comparator.comparingInt(p -> distSq(p, center)));

        JsonArray blocks = new JsonArray();
        int n = Math.min(max, hits.size());
        for (int i = 0; i < n; i++) {
            BlockPos pos = hits.get(i);
            ResourceLocation id = level.registryAccess()
                    .registryOrThrow(Registries.BLOCK).getKey(level.getBlockState(pos).getBlock());
            JsonObject b = new JsonObject();
            b.addProperty("x", pos.getX());
            b.addProperty("y", pos.getY());
            b.addProperty("z", pos.getZ());
            b.addProperty("id", id == null ? "?" : id.toString());
            blocks.add(b);
        }

        JsonObject result = new JsonObject();
        result.add("blocks", blocks);
        JsonObject summary = new JsonObject();
        summary.add("underfoot", blockEntry(level, center.below()));
        summary.add("front", blockEntry(level, frontPos(center, player.getLookAngle())));
        summary.add("head", blockEntry(level, center.above()));
        result.add("summary", summary);
        result.addProperty("truncated", hits.size() > max);
        return result;
    }

    private static int distSq(BlockPos p, BlockPos center) {
        int dx = p.getX() - center.getX();
        int dy = p.getY() - center.getY();
        int dz = p.getZ() - center.getZ();
        return dx * dx + dy * dy + dz * dz;
    }

    /** 视线方向 1 格（取水平方向主轴向的相邻方块）。 */
    private static BlockPos frontPos(BlockPos center, net.minecraft.world.phys.Vec3 look) {
        Direction dir = Direction.getNearest(look.x, 0, look.z);
        return center.offset(dir.getNormal());
    }

    /** 单个方块条目 {@code {id, x, y, z}}（summary 用，空气也列出）。 */
    private static JsonObject blockEntry(Level level, BlockPos pos) {
        BlockState state = level.getBlockState(pos);
        ResourceLocation id = level.registryAccess()
                .registryOrThrow(Registries.BLOCK).getKey(state.getBlock());
        JsonObject o = new JsonObject();
        o.addProperty("id", id == null ? "?" : id.toString());
        o.addProperty("x", pos.getX());
        o.addProperty("y", pos.getY());
        o.addProperty("z", pos.getZ());
        return o;
    }
}
