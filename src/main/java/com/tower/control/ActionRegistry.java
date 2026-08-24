package com.tower.control;

import java.util.HashMap;
import java.util.Map;

/**
 * 动作注册表：action 名 → 处理器（协议 §5 动作定义）。
 *
 * <p>M1.3 注册前置转发动作全集（20 个，§5.1）；get_state 与感知/导航/视觉
 * 原生动作在 M2+ 逐个注册。
 *
 * <p>线程安全：注册在 mod 初始化（主线程）完成；运行期只读，无并发写入。
 */
public final class ActionRegistry {
    private static final Map<String, ActionHandler> HANDLERS = new HashMap<>();

    private ActionRegistry() {
    }

    public static void register(String action, ActionHandler handler) {
        HANDLERS.put(action, handler);
    }

    /** 未注册返回 null（Dispatcher 回 101）。 */
    public static ActionHandler get(String action) {
        return HANDLERS.get(action);
    }
}
