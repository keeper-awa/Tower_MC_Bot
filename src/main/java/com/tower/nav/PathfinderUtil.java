package com.tower.nav;

import com.mojang.logging.LogUtils;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.monster.Zombie;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.PathNavigationRegion;
import net.minecraft.world.level.pathfinder.Path;
import net.minecraft.world.level.pathfinder.PathFinder;
import net.minecraft.world.level.pathfinder.WalkNodeEvaluator;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * 原版寻路封装（《Tower协议.md》§5.3）：PathFinder + WalkNodeEvaluator，
 * dummy 僵尸代理（不加入世界），返回路径点列表。
 *
 * <p>配置：可穿过/打开木门（canPassDoors/canOpenDoors，原版将关着的木门
 * DOOR_WOOD_CLOSED 视为可通行路径类型）；allow_water 控制深水穿越；
 * 目标自动向下扫 16 格找最近可行走地面。
 *
 * <p>线程：仅在游戏主线程调用（寻路同步计算，单次一般 &lt; 10ms）。
 */
public final class PathfinderUtil {
    private static final Logger LOGGER = LogUtils.getLogger();
    /** 寻路最大范围（格）。 */
    public static final int MAX_RANGE = 64;
    /** A* 节点访问上限（防极端场景耗时）。 */
    private static final int MAX_VISITED_NODES = 1500;
    /** 目标向下找地面深度。 */
    private static final int GROUND_SCAN_DEPTH = 16;

    private PathfinderUtil() {
    }

    /**
     * 计算从玩家位置到目标的路径点（不含起点）。
     *
     * @return 路径点列表（{@code BlockPos}）；不可达返回 null
     */
    public static List<BlockPos> findWaypoints(LocalPlayer player, BlockPos target, boolean allowWater) {
        Level level = player.level();
        Vec3 start = player.position();
        // dummy 僵尸代理：仅提供寻路的起始位置/尺寸，不加入世界
        Zombie dummy = new Zombie(EntityType.ZOMBIE, level);
        dummy.setPos(start.x, start.y, start.z);
        dummy.setYRot(player.getYRot());

        // 寻路区域（PathNavigationRegion 惰性取块，构造开销小）
        BlockPos startPos = player.blockPosition();
        int pad = MAX_RANGE + 16;
        PathNavigationRegion region = new PathNavigationRegion(level,
                startPos.offset(-pad, -GROUND_SCAN_DEPTH, -pad),
                startPos.offset(pad, GROUND_SCAN_DEPTH, pad));

        WalkNodeEvaluator evaluator = new WalkNodeEvaluator();
        evaluator.setCanPassDoors(true);
        evaluator.setCanOpenDoors(true);
        evaluator.setCanFloat(allowWater);
        PathFinder finder = new PathFinder(evaluator, MAX_VISITED_NODES);

        // 目标超出搜索范围预检：直接 304，避免 A* 穷尽后 bestNode=null 的 NPE
        if (Math.abs(target.getX() - startPos.getX())
                + Math.abs(target.getY() - startPos.getY())
                + Math.abs(target.getZ() - startPos.getZ()) > MAX_RANGE) {
            return null;
        }

        BlockPos groundTarget = findGround(level, target);
        Path path;
        try {
            // findPath 参数：maxRange=搜索半径（离起点超此距离不展开）；
            // depth=到达判定半径（node 距目标**曼哈顿距离** <= depth 即视为到达）——
            // 必须传小值（1），否则起点距目标 < depth 时立即"到达"，路径只剩起点
            path = finder.findPath(region, dummy, Set.of(groundTarget), MAX_RANGE, 1, 1.0F);
        } catch (Exception e) {
            LOGGER.warn("Tower: 寻路计算异常（按不可达处理）", e);
            return null;
        }
        if (path == null || path.isDone()) {
            return null; // 不可达
        }

        List<BlockPos> waypoints = new ArrayList<>();
        for (int i = path.getNextNodeIndex(); i < path.getNodeCount(); i++) {
            waypoints.add(path.getNodePos(i));
        }
        if (waypoints.isEmpty()) {
            return null;
        }
        LOGGER.debug("Tower: 寻路成功 目标={} 路径点={} 个", groundTarget, waypoints.size());
        return waypoints;
    }

    /** 目标位置自动找最近可行走地面（向下扫，方块上方为空气）。 */
    private static BlockPos findGround(Level level, BlockPos target) {
        for (int y = target.getY(); y >= target.getY() - GROUND_SCAN_DEPTH; y--) {
            BlockPos pos = new BlockPos(target.getX(), y, target.getZ());
            if (!level.getBlockState(pos).isAir() && level.getBlockState(pos.above()).isAir()) {
                return pos;
            }
        }
        return target;
    }
}
