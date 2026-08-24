package com.tower;

import com.mojang.logging.LogUtils;
import com.tower.config.ConfigManager;
import com.tower.config.PrereqConfig;
import com.tower.control.ActionRegistry;
import com.tower.control.ForwardAction;
import com.tower.control.GetBlocksAction;
import com.tower.control.GetEntitiesAction;
import com.tower.control.GetStateAction;
import com.tower.control.MoveToAction;
import com.tower.control.RaycastAction;
import com.tower.control.ScreenshotAction;
import com.tower.nav.NavController;
import com.tower.net.WsServer;
import com.tower.relay.PrereqClient;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.GameShuttingDownEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.loading.FMLEnvironment;
import net.minecraftforge.fml.loading.FMLPaths;
import org.slf4j.Logger;

/**
 * Tower 入口（仅客户端，mods.toml 声明 clientOnly）。
 *
 * <p>按《功能列表.md》里程碑逐步接入：
 * M1.1 脚手架与配置（已完成）→ M1.2 Tower WS 服务器（已完成）→
 * M1.3 前置转发链路（本阶段：20 动作转发 + 9 事件中继 + 断线归零序列）
 * → M2 感知 → M3 寻路 → M4 截图 → M5-M7 大脑与交付。
 *
 * <p>注意：1.20.1 的 NeoForge(47.x) 仍使用 {@code net.minecraftforge.*} API 包名。
 */
@Mod(TowerMod.MODID)
public class TowerMod {
    public static final String MODID = "tower";

    /** 转发动作全集（《Tower协议.md》§5.1）：原样透传前置，Tower 不校验不修改。 */
    private static final String[] FORWARDED_ACTIONS = {
            "move", "jump", "jump_once", "sneak", "sprint", "swim", "fly", "fall_fly", "look_at",
            "attack", "use_item", "interact_block", "interact_entity", "drop", "hotbar", "chat",
            "set_push", "equip", "move_item", "craft",
    };

    private static final Logger LOGGER = LogUtils.getLogger();

    public TowerMod() {
        if (FMLEnvironment.dist == Dist.CLIENT) {
            ConfigManager.init(FMLPaths.CONFIGDIR.get());
            PrereqConfig.init(FMLPaths.CONFIGDIR.get());
            registerActions();
            WsServer.start();
            PrereqClient.start();
            MinecraftForge.EVENT_BUS.register(this);
        } else {
            LOGGER.info("Tower mod skipped (client-only, dist={})", FMLEnvironment.dist);
        }
    }

    /** 游戏退出时释放端口与事件循环（主菜单直接退出场景由 shutdown hook 兜底）。 */
    @SubscribeEvent
    public void onGameShuttingDown(GameShuttingDownEvent event) {
        NavController.stop();
        PrereqClient.shutdown();
        WsServer.shutdown();
    }

    /** 客户端每 tick：导航状态机驱动（游戏主线程）。 */
    @SubscribeEvent
    public void onClientTick(net.minecraftforge.event.TickEvent.ClientTickEvent event) {
        if (event.phase == net.minecraftforge.event.TickEvent.Phase.END) {
            NavController.onClientTick();
        }
    }

    /** 注册协议动作：前置转发动作全集（§5.1）+ Tower 原生动作（§5.2-§5.5）。 */
    private static void registerActions() {
        for (String action : FORWARDED_ACTIONS) {
            ActionRegistry.register(action, new ForwardAction(action));
        }
        // M2 感知层（Tower 原生，主线程执行）
        ActionRegistry.register("get_state", new GetStateAction());
        ActionRegistry.register("raycast", new RaycastAction());
        ActionRegistry.register("get_blocks", new GetBlocksAction());
        ActionRegistry.register("get_entities", new GetEntitiesAction());
        // M3 导航层
        ActionRegistry.register("move_to", new MoveToAction());
        // M4 视觉层
        ActionRegistry.register("screenshot", new ScreenshotAction());
    }
}
