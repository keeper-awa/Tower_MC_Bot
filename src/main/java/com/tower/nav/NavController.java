package com.tower.nav;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.logging.LogUtils;
import com.tower.control.ActionException;
import com.tower.relay.EventRelay;
import com.tower.relay.PrereqClient;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Options;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;

import java.util.ArrayList;
import java.util.List;

/**
 * move_to 自动驾驶（《Tower协议.md》§5.3）：状态机 + 客户端 tick 驱动。
 *
 * <p>**执行动作全部经转发链路**（move/look_at/jump_once/interact_block/sprint 由
 * 前置执行——复用 M1.3 已验证链路，与前置输入注入解耦）；look_at 为持续锁定，
 * 仅节点切换时发送；move 仅状态变化时发送。
 *
 * <p>状态：{@code IDLE → NAVIGATING ⇄ PAUSED → IDLE}。
 *
 * <p>行为（协议 §5.3）：
 * <ul>
 *   <li>沿路径点：look_at 对准当前节点 + move 前进；水平距离 &lt; precision → 下一节点</li>
 *   <li>自动开木门：前方/当前节点是关着的木门 → interact_block（带节流）</li>
 *   <li>卡住处理：40 tick 位移 &lt; 0.1 → 先 jump_once（台阶）→ 再卡 → path_stuck + 重算；
 *       连续 2 次重算仍卡 → path_failed {reason:"stuck"}</li>
 *   <li>手动干预（D5）：W/A/D = 暂停（松手恢复，走远重算）；S = 取消</li>
 *   <li>path_progress 每 20 tick（≈1s）</li>
 * </ul>
 *
 * <p>取消途径：move_to {cancel:true}（requested）、后退键（manual_backward）、
 * 大脑断线（disconnect，由 WsSessionHandler 调用）、path_failed 后自动结束。
 *
 * <p>线程：全部方法仅在游戏主线程调用（MoveToAction 经 NativeAction 封送，
 * tick 驱动经 ClientTickEvent）。
 */
public final class NavController {
    private static final Logger LOGGER = LogUtils.getLogger();
    /** path_progress 推送间隔（tick）。 */
    private static final int PROGRESS_INTERVAL = 20;
    /** 卡住检测窗口（tick）。 */
    private static final int STUCK_WINDOW = 40;
    /** 卡住位移阈值（格）。 */
    private static final double STUCK_THRESHOLD = 0.1;
    /** 暂停后走远重算阈值（格）。 */
    private static final double PAUSE_OFF_PATH = 16.0;
    /** 路径点事件截断上限（协议 §5.3：路径点 > 128 截断）。 */
    private static final int WAYPOINT_EVENT_LIMIT = 128;
    /** 自动开门节流（tick）。 */
    private static final long DOOR_OPEN_INTERVAL = 40;

    private enum State {
        IDLE, NAVIGATING, PAUSED
    }

    private static State state = State.IDLE;
    private static BlockPos target;
    private static List<BlockPos> waypoints = new ArrayList<>();
    private static int nodeIndex;
    private static boolean sprint;
    private static float precision;
    private static int tickCounter;
    private static int stuckTicks;
    private static int stuckTries;
    private static int recomputeTries;
    private static Vec3 lastStuckPos;
    private static BlockPos lastDoorPos;
    private static long lastDoorOpenTick;

    /** 前置转发请求无回调占位（自动驾驶 fire-and-forget）。 */
    private static final PrereqClient.Callback NOOP = new PrereqClient.Callback() {
        @Override
        public void onResult(com.google.gson.JsonElement result) {
        }

        @Override
        public void onError(int code, String message) {
            LOGGER.debug("Tower: 导航动作失败 code={} {}", code, message);
        }
    };

    private NavController() {
    }

    /** 当前是否有进行中的导航（供 MoveToAction 判断/其他模块查询）。 */
    public static boolean isActive() {
        return state != State.IDLE;
    }

    /**
     * 启动导航（由 MoveToAction 在主线程调用）。
     *
     * @return 响应 result：waypoints 模式 {@code {status:"ok",...}}，auto 模式 {@code {status:"started",...}}
     * @throws ActionException 不可达 → 304
     */
    public static JsonObject start(BlockPos target, String mode, boolean allowWater, boolean sprint, float precision)
            throws ActionException {
        // 先取消当前导航（静默，不发取消事件）
        reset();

        LocalPlayer player = Minecraft.getInstance().player;
        List<BlockPos> path = PathfinderUtil.findWaypoints(player, target, allowWater);
        if (path == null) {
            throw new ActionException(304, "目标不可达（路径不存在）");
        }

        NavController.target = target;
        NavController.waypoints = path;
        NavController.nodeIndex = 0;
        NavController.sprint = sprint;
        NavController.precision = precision;
        NavController.tickCounter = 0;
        NavController.stuckTicks = 0;
        NavController.stuckTries = 0;
        NavController.recomputeTries = 0;
        NavController.lastStuckPos = null;

        // path_found 事件（auto 与 waypoints 均推送，协议 §5.3）
        sendEvent("path_found", pathEvent("auto".equals(mode) ? "auto" : "waypoints"));

        if ("waypoints".equals(mode)) {
            JsonObject r = new JsonObject();
            r.addProperty("status", "ok");
            r.add("waypoints", waypointsJson());
            r.addProperty("total", waypoints.size());
            r.addProperty("truncated", waypoints.size() > WAYPOINT_EVENT_LIMIT);
            return r;
        }

        state = State.NAVIGATING;
        send("look_at", nodeJson(waypoints.get(0)));
        send("move", moveJson(true));
        if (sprint) {
            send("sprint", valueJson(true));
        }
        LOGGER.info("Tower: move_to 自动驾驶开始 目标=({},{},{}) 路径点={} 个", target.getX(), target.getY(), target.getZ(),
                waypoints.size());
        JsonObject r = new JsonObject();
        r.addProperty("status", "started");
        r.addProperty("mode", "auto");
        r.add("target", posJson(target));
        return r;
    }

    /** 取消导航（move_to cancel / 后退键 / 大脑断线）。 */
    public static void cancel(String reason) {
        if (state == State.IDLE) {
            return;
        }
        state = State.IDLE;
        send("move", moveJson(false));
        send("look_at", new JsonObject());
        if (sprint) {
            send("sprint", valueJson(false));
        }
        sendEvent("path_cancelled", reasonJson(reason));
        LOGGER.info("Tower: 导航取消（{}）", reason);
        reset();
    }

    /** 客户端每 tick 驱动（TowerMod ClientTickEvent END）。 */
    public static void onClientTick() {
        if (state == State.IDLE) {
            return;
        }
        Minecraft mc = Minecraft.getInstance();
        Options o = mc.options;
        LocalPlayer player = mc.player;
        if (player == null) {
            return;
        }

        if (state == State.NAVIGATING) {
            // 手动干预（D5）：W/A/D 暂停，S 取消
            if (o.keyUp.isDown() || o.keyLeft.isDown() || o.keyRight.isDown()) {
                doPause();
                return;
            }
            if (o.keyDown.isDown()) {
                cancel("manual_backward");
                return;
            }
            drive(player);
        } else { // PAUSED
            if (o.keyDown.isDown()) {
                cancel("manual_backward");
                return;
            }
            if (!(o.keyUp.isDown() || o.keyLeft.isDown() || o.keyRight.isDown())) {
                doResume(player);
            }
        }
    }

    /** 游戏退出清理（TowerMod GameShuttingDown）。 */
    public static void stop() {
        if (state != State.IDLE) {
            send("move", moveJson(false));
            send("look_at", new JsonObject());
        }
        reset();
    }

    // ── 自动驾驶驱动 ──────────────────────────────────────────────

    private static void drive(LocalPlayer player) {
        tickCounter++;
        BlockPos node = waypoints.get(nodeIndex);

        // 1. 到达判定（水平距离 < precision → 下一节点）
        if (horizontalDist(player.position(), node) < precision) {
            nodeIndex++;
            if (nodeIndex >= waypoints.size()) {
                finishReached(player);
                return;
            }
            node = waypoints.get(nodeIndex);
            send("look_at", nodeJson(node)); // 对准新节点（look_at 持续锁定）
        }

        // 2. 自动开木门（前方 1 格或当前节点是关着的木门，带节流）
        tryOpenDoor(player, node);

        // 3. 卡住检测（40 tick 窗口位移 < 0.1）
        stuckTicks++;
        if (stuckTicks >= STUCK_WINDOW) {
            stuckTicks = 0;
            Vec3 pos = player.position();
            if (lastStuckPos != null && horizontalDist(pos, lastStuckPos) < STUCK_THRESHOLD) {
                handleStuck(player);
                return;
            }
            lastStuckPos = pos;
        }

        // 4. path_progress（每 20 tick ≈1s）
        if (tickCounter % PROGRESS_INTERVAL == 0) {
            sendProgress(player, node);
        }
    }

    private static void handleStuck(LocalPlayer player) {
        if (stuckTries == 0) {
            // 第一次卡住：自动 jump_once（上 1 格台阶场景），重置窗口再观察
            stuckTries = 1;
            lastStuckPos = null;
            send("jump_once", new JsonObject());
            LOGGER.debug("Tower: 导航疑似卡住，尝试跳跃");
            return;
        }
        // 仍然卡住：path_stuck + 重算路径
        stuckTries = 0;
        lastStuckPos = null;
        recomputeTries++;
        sendEvent("path_stuck", triesJson(recomputeTries));
        if (recomputeTries >= 2) {
            // 连续 2 次重算仍卡 → 失败
            finishFailed("stuck", "连续 2 次重算仍卡住");
            return;
        }
        recomputePath(player);
    }

    /** 重新寻路（卡住重算 / 暂停走远重算）；失败 → path_failed。 */
    private static void recomputePath(LocalPlayer player) {
        List<BlockPos> path = PathfinderUtil.findWaypoints(player, target, false);
        if (path == null) {
            finishFailed("no_path", "重算路径不存在");
            return;
        }
        waypoints = path;
        nodeIndex = 0;
        sendEvent("path_found", pathEvent("auto"));
        send("look_at", nodeJson(waypoints.get(0)));
        LOGGER.info("Tower: 导航路径已重算（{} 个路径点）", waypoints.size());
    }

    private static void tryOpenDoor(LocalPlayer player, BlockPos node) {
        Level level = player.level();
        BlockPos ahead = player.blockPosition().offset(
                (int) Math.round(player.getLookAngle().x),
                0,
                (int) Math.round(player.getLookAngle().z));
        for (BlockPos pos : new BlockPos[]{ahead, node}) {
            BlockState st = level.getBlockState(pos);
            if (st.is(BlockTags.WOODEN_DOORS) && !st.getValue(DoorBlock.OPEN)) {
                long now = level.getGameTime();
                if (pos.equals(lastDoorPos) && now - lastDoorOpenTick < DOOR_OPEN_INTERVAL) {
                    return; // 节流：同一扇门 40 tick 内只开一次
                }
                lastDoorPos = pos;
                lastDoorOpenTick = now;
                send("interact_block", posJson(pos));
                LOGGER.info("Tower: 导航自动开门 @ ({},{},{})", pos.getX(), pos.getY(), pos.getZ());
                return;
            }
        }
    }

    // ── 暂停/恢复（D5）───────────────────────────────────────────

    private static void doPause() {
        state = State.PAUSED;
        send("move", moveJson(false));   // 完全让出控制
        send("look_at", new JsonObject()); // 解除视角锁定
        if (sprint) {
            send("sprint", valueJson(false));
        }
        sendEvent("path_paused", reasonJson("manual"));
        LOGGER.info("Tower: 导航暂停（手动按键）");
    }

    private static void doResume(LocalPlayer player) {
        // 暂停期间玩家走远（偏离路径 > 16 格）→ 自动重算
        if (horizontalDist(player.position(), waypoints.get(nodeIndex)) > PAUSE_OFF_PATH) {
            LOGGER.info("Tower: 暂停期间走远，重算路径");
            recomputePath(player);
        }
        state = State.NAVIGATING;
        send("look_at", nodeJson(waypoints.get(nodeIndex)));
        send("move", moveJson(true));
        if (sprint) {
            send("sprint", valueJson(true));
        }
        sendEvent("path_resumed", new JsonObject());
        LOGGER.info("Tower: 导航恢复");
    }

    // ── 结束 ─────────────────────────────────────────────────────

    private static void finishReached(LocalPlayer player) {
        state = State.IDLE;
        send("move", moveJson(false));
        send("look_at", new JsonObject());
        if (sprint) {
            send("sprint", valueJson(false));
        }
        JsonObject data = new JsonObject();
        data.addProperty("x", target.getX());
        data.addProperty("y", target.getY());
        data.addProperty("z", target.getZ());
        sendEvent("path_reached", data);
        LOGGER.info("Tower: 导航到达目标 ({},{},{})", target.getX(), target.getY(), target.getZ());
        reset();
    }

    private static void finishFailed(String reason, String detail) {
        state = State.IDLE;
        send("move", moveJson(false));
        send("look_at", new JsonObject());
        if (sprint) {
            send("sprint", valueJson(false));
        }
        JsonObject data = reasonJson(reason);
        if (detail != null) {
            data.addProperty("detail", detail);
        }
        sendEvent("path_failed", data);
        LOGGER.warn("Tower: 导航失败（{}: {}）", reason, detail);
        reset();
    }

    private static void reset() {
        state = State.IDLE;
        target = null;
        waypoints = new ArrayList<>();
        nodeIndex = 0;
        sprint = false;
        tickCounter = 0;
        stuckTicks = 0;
        stuckTries = 0;
        recomputeTries = 0;
        lastStuckPos = null;
        lastDoorPos = null;
    }

    // ── 事件与转发请求构造 ────────────────────────────────────────

    private static void sendEvent(String event, JsonObject data) {
        JsonObject msg = new JsonObject();
        msg.addProperty("type", "event");
        msg.addProperty("event", event);
        msg.add("data", data);
        EventRelay.relay(msg);
    }

    /** 转发动作（fire-and-forget）；前置链路断开时动作失效（玩家自然停止，安全）。 */
    private static void send(String action, JsonObject params) {
        PrereqClient.sendRequest(action, params, NOOP);
    }

    private static JsonObject pathEvent(String mode) {
        JsonObject d = new JsonObject();
        d.addProperty("mode", mode);
        d.add("target", posJson(target));
        d.addProperty("total", waypoints.size());
        d.add("waypoints", waypointsJson());
        d.addProperty("truncated", waypoints.size() > WAYPOINT_EVENT_LIMIT);
        return d;
    }

    private static void sendProgress(LocalPlayer player, BlockPos node) {
        JsonObject d = new JsonObject();
        d.addProperty("remaining", player.position().distanceTo(
                new Vec3(target.getX() + 0.5, target.getY(), target.getZ() + 0.5)));
        d.addProperty("node_index", nodeIndex);
        d.add("node", posJson(node));
        sendEvent("path_progress", d);
    }

    /** 路径点数组（协议 §5.3：> 128 截断，truncated 标记由调用方给出）。 */
    private static JsonArray waypointsJson() {
        JsonArray arr = new JsonArray();
        int limit = Math.min(waypoints.size(), WAYPOINT_EVENT_LIMIT);
        for (int i = 0; i < limit; i++) {
            arr.add(posJson(waypoints.get(i)));
        }
        return arr;
    }

    private static JsonObject posJson(BlockPos p) {
        JsonObject o = new JsonObject();
        o.addProperty("x", p.getX());
        o.addProperty("y", p.getY());
        o.addProperty("z", p.getZ());
        return o;
    }

    private static JsonObject nodeJson(BlockPos p) {
        return posJson(p);
    }

    private static JsonObject moveJson(boolean forward) {
        JsonObject o = new JsonObject();
        o.addProperty("forward", forward ? 1 : 0);
        return o;
    }

    private static JsonObject valueJson(boolean value) {
        JsonObject o = new JsonObject();
        o.addProperty("value", value);
        return o;
    }

    private static JsonObject reasonJson(String reason) {
        JsonObject o = new JsonObject();
        o.addProperty("reason", reason);
        return o;
    }

    private static JsonObject triesJson(int tries) {
        JsonObject o = new JsonObject();
        o.addProperty("tries", tries);
        return o;
    }

    private static double horizontalDist(Vec3 a, BlockPos b) {
        double dx = a.x - (b.getX() + 0.5);
        double dz = a.z - (b.getZ() + 0.5);
        return Math.sqrt(dx * dx + dz * dz);
    }

    private static double horizontalDist(Vec3 a, Vec3 b) {
        double dx = a.x - b.x;
        double dz = a.z - b.z;
        return Math.sqrt(dx * dx + dz * dz);
    }
}
