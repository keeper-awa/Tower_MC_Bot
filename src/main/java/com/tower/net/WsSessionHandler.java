package com.tower.net;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import com.google.gson.JsonParser;
import com.mojang.logging.LogUtils;
import com.tower.nav.NavController;
import com.tower.relay.EventRelay;
import com.tower.relay.PrereqClient;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.SimpleChannelInboundHandler;
import io.netty.handler.timeout.IdleStateEvent;
import org.slf4j.Logger;

/**
 * WS 会话层：token 鉴权 → hello 宣告；ping/pong 心跳；读空闲超时断开（协议 §2）。
 *
 * <p>鉴权失败：仍完成 WS 握手，发送 {@code hello{ok:false,error:auth_failed}} 后断开
 * （协议 §2，close 码 1008 Policy Violation）。
 * 鉴权成功：注册单连接（挤掉旧连接），发送
 * {@code hello{ok:true,protocol:1,mod:"tower",version,prereq}}——prereq 为前置
 * 转发链路状态（M1.3 起实时查询 {@link PrereqClient}）。
 *
 * <p>M1.3 起：request 由 {@link Dispatcher} 校验与路由（转发动作 → 前置）；
 * 大脑连接建立时恢复前置链路 + 重放 player_ready（中继自前置）；
 * 大脑断开时执行协议 §2.4 断线清理（主动归零序列 → 断开前置双保险）。
 */
public class WsSessionHandler extends SimpleChannelInboundHandler<String> {
    private static final Logger LOGGER = LogUtils.getLogger();

    /** 协议版本（《Tower协议.md》§1），破坏性变更时递增。 */
    public static final int PROTOCOL_VERSION = 1;
    static final String MOD_ID = "tower";
    static final String MOD_VERSION = "0.1.0";

    /** 前置 mod 连接状态（协议 §2.1 hello.prereq）：connected / disconnected。 */
    private static String prereqStatus() {
        return PrereqClient.isConnected() ? "connected" : "disconnected";
    }

    @Override
    public void channelActive(ChannelHandlerContext ctx) {
        String token = ctx.channel().attr(WsHandshakeHandler.TOKEN_KEY).get();
        if (!AuthManager.verify(token)) {
            ctx.writeAndFlush("{\"type\":\"hello\",\"ok\":false,\"error\":\"auth_failed\"}");
            ctx.writeAndFlush(WsFrameEncoder.closeFrame(ctx.alloc(), 1008));
            ctx.close();
            LOGGER.warn("Tower: 鉴权失败（token 不匹配），已断开");
            return;
        }
        AuthManager.register(ctx.channel());
        LOGGER.info("Tower: 客户端鉴权通过，已建立会话");
        ctx.writeAndFlush("{\"type\":\"hello\",\"ok\":true,\"protocol\":" + PROTOCOL_VERSION
                + ",\"mod\":\"" + MOD_ID + "\",\"version\":\"" + MOD_VERSION
                + "\",\"prereq\":\"" + prereqStatus() + "\"}");
        // 大脑重连：恢复前置链路（协议 §2.4 ⑤）+ 重放中继的 player_ready（协议 §10）
        PrereqClient.ensureConnected();
        EventRelay.replayPlayerReady(ctx.channel());
    }

    @Override
    protected void channelRead0(ChannelHandlerContext ctx, String message) {
        JsonObject obj;
        try {
            obj = JsonParser.parseString(message).getAsJsonObject();
        } catch (JsonParseException | IllegalStateException e) {
            // 104 消息格式错误（非法 JSON / 非对象）
            ctx.writeAndFlush(MessageCodec.errorResponse(0, MessageCodec.ERR_BAD_FORMAT, "消息必须是 JSON 对象"));
            return;
        }
        JsonElement typeEl = obj.get("type");
        String type = typeEl == null || typeEl.isJsonNull() ? "" : typeEl.getAsString();
        if ("ping".equals(type)) {
            ctx.writeAndFlush("{\"type\":\"pong\"}");
        } else if ("request".equals(type)) {
            // 校验与路由由 Dispatcher 完成（协议 §4/§8）
            Dispatcher.dispatch(ctx.channel(), obj);
        } else {
            // 服务端→客户端方向的消息（response/event/hello）客户端不应发送
            ctx.writeAndFlush(MessageCodec.errorResponse(0, MessageCodec.ERR_BAD_FORMAT, "消息类型错误: " + type));
        }
    }

    @Override
    public void userEventTriggered(ChannelHandlerContext ctx, Object evt) {
        if (evt instanceof IdleStateEvent) {
            LOGGER.info("Tower: 连接读空闲超时（60s 无消息），断开（协议 §2.4）");
            ctx.close();
        } else {
            ctx.fireUserEventTriggered(evt);
        }
    }

    @Override
    public void channelInactive(ChannelHandlerContext ctx) {
        AuthManager.unregister(ctx.channel());
        LOGGER.info("Tower: 连接已断开，执行断线清理（协议 §2.4）");
        // 断线清理：① 主动归零序列（attack release 等）→ ② 断开前置（双保险）→ ③ 取消导航
        PrereqClient.zeroAndDisconnect();
        NavController.cancel("disconnect");
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
        LOGGER.error("Tower: 连接异常，断开", cause);
        ctx.close();
    }
}
