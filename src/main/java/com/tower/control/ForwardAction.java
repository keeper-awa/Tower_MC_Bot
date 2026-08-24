package com.tower.control;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.tower.net.MessageCodec;
import com.tower.relay.PrereqClient;

/**
 * 转发动作（协议 §5.1）：大脑请求原样转发给前置 mod，前置的响应/错误原样透传。
 *
 * <p>不校验、不修改参数（协议 §5.1：Tower 只做透传）；前置未连接 → 501
 * （协议 §8.2）。异步执行：等待前置响应后经回调写回大脑连接。
 *
 * <p>线程模型：执行在大脑侧 Netty 线程（纯网络）；回调在 PrereqClient
 * eventLoop（写大脑 channel 线程安全）。
 */
public final class ForwardAction implements ActionHandler {
    private final String action;

    public ForwardAction(String action) {
        this.action = action;
    }

    @Override
    public JsonElement handle(ActionContext ctx, JsonObject params) throws ActionException {
        boolean sent = PrereqClient.sendRequest(action, params, new PrereqClient.Callback() {
            @Override
            public void onResult(JsonElement result) {
                ctx.channel.writeAndFlush(MessageCodec.successResponse(ctx.id, result));
            }

            @Override
            public void onError(int code, String message) {
                ctx.channel.writeAndFlush(MessageCodec.errorResponse(ctx.id, code, message));
            }
        });
        if (!sent) {
            throw new ActionException(501, "前置 mod 未连接（转发动作不可用）");
        }
        return null; // 异步响应
    }
}
