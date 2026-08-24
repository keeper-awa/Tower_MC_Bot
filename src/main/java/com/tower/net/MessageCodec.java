package com.tower.net;

import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/**
 * 消息编解码与响应构造（《Tower协议.md》§3/§4/§8）。
 *
 * <p>职责：响应 JSON 序列化（成功/失败）与错误码常量。
 * 请求校验见后续里程碑的 Dispatcher（M1.3 转发链路接入）；
 * 帧级 64KB 上限由 {@link WsFrameDecoder} 实现（close 1009，对应协议 105 的底层形态）。
 *
 * <p>线程安全：Gson 线程安全，可在任意线程调用。
 */
public final class MessageCodec {
    private static final Gson GSON = new Gson();

    /** 错误码（《Tower协议.md》§8：透传前置码 101-105/301-304/401 + Tower 新增 501）。 */
    public static final int ERR_UNKNOWN_ACTION = 101;
    public static final int ERR_MISSING_PARAM = 102;
    public static final int ERR_INVALID_PARAM = 103;
    public static final int ERR_BAD_FORMAT = 104;
    public static final int ERR_TOO_LARGE = 105;
    public static final int ERR_INTERNAL = 401;

    private MessageCodec() {
    }

    /** 成功响应：{@code {"id":N,"ok":true,"result":{...}}}（result 为 null 时用空对象）。 */
    public static String successResponse(long id, JsonElement result) {
        JsonObject resp = new JsonObject();
        resp.addProperty("id", id);
        resp.addProperty("ok", true);
        resp.add("result", result == null ? new JsonObject() : result);
        return GSON.toJson(resp);
    }

    /** 失败响应：{@code {"id":N,"ok":false,"error":{"code":C,"message":"..."}}}。 */
    public static String errorResponse(long id, int code, String message) {
        JsonObject error = new JsonObject();
        error.addProperty("code", code);
        error.addProperty("message", message);
        JsonObject resp = new JsonObject();
        resp.addProperty("id", id);
        resp.addProperty("ok", false);
        resp.add("error", error);
        return GSON.toJson(resp);
    }
}
