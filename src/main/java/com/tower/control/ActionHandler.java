package com.tower.control;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/**
 * 动作处理器（协议 §5 各动作的入口）。
 *
 * <p>约定（《Tower协议.md》§4）：返回成功响应的 {@code result}
 * （JSON 元素，可 null → 空对象）；失败抛 {@link ActionException} 携带错误码。
 *
 * <p>返回 {@code null} 表示**异步响应**：处理器已启动异步流程，成功/失败响应
 * 由处理器稍后经回调发送（如 M1.3 转发动作——需等待前置响应后回给大脑）。
 */
@FunctionalInterface
public interface ActionHandler {
    JsonElement handle(ActionContext ctx, JsonObject params) throws ActionException;
}
