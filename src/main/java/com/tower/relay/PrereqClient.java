package com.tower.relay;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.logging.LogUtils;
import com.tower.config.PrereqConfig;
import com.tower.net.MessageCodec;
import com.tower.net.WsClientFrameDecoder;
import com.tower.net.WsClientFrameEncoder;
import io.netty.bootstrap.Bootstrap;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.Channel;
import io.netty.channel.ChannelFutureListener;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.ChannelInitializer;
import io.netty.channel.ChannelOption;
import io.netty.channel.EventLoopGroup;
import io.netty.channel.SimpleChannelInboundHandler;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.SocketChannel;
import io.netty.channel.socket.nio.NioSocketChannel;
import io.netty.handler.codec.ByteToMessageDecoder;
import io.netty.util.concurrent.DefaultThreadFactory;
import io.netty.util.concurrent.ScheduledFuture;
import org.slf4j.Logger;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

/**
 * 前置 mod 转发链路（《Tower协议.md》§5.1/§7/§2.4）：
 * Tower 内置 WS 客户端连接前置（24777），转发执行动作、中继事件。
 *
 * <p>连接参数（决策 A）：每次连接/重连时读前置 {@code config/keyboard.json}
 * （port+token），token 轮换自动跟随；失败每 3s 重试（前置可能晚于 Tower 启动）。
 *
 * <p>生命周期：
 * <ul>
 *   <li>{@link #start()}：mod 初始化时启动（预热链路，自动重连开）</li>
 *   <li>大脑断线（协议 §2.4）：{@link #zeroAndDisconnect()} —— 先发主动归零序列
 *       （attack release / move / jump / sneak / look_at / 姿态全关），补前置断线归零
 *       不覆盖 attack hold 的缺口，再断开前置（触发其自身归零，双保险），
 *       关闭自动重连；大脑重连时 {@link #ensureConnected()} 恢复</li>
 *   <li>前置链路意外断开（前置崩溃/重启）：保持自动重连，持续恢复并更新 prereq 状态</li>
 * </ul>
 *
 * <p>线程模型：全部状态仅在自身 eventLoop 线程访问（外部入口均封送 eventLoop）；
 * {@code connected} 为 volatile 供大脑侧 hello/501 判断；响应回调经 Netty
 * channel 写大脑连接（线程安全）。
 */
public final class PrereqClient {
    private static final Logger LOGGER = LogUtils.getLogger();
    /** 重连间隔（前置启动可能晚于 Tower）。 */
    private static final long RECONNECT_DELAY_SECONDS = 3;
    /** 心跳间隔（前置 60s 空闲断开，协议 §2.4 心跳 ≤30s）。 */
    private static final long PING_INTERVAL_SECONDS = 25;
    /** 转发请求响应超时（前置正常毫秒级响应；超时按 401 内部错误回大脑）。 */
    private static final long REQUEST_TIMEOUT_SECONDS = 10;
    /** 握手请求/响应大小上限。 */
    private static final int MAX_HANDSHAKE_BYTES = 16 * 1024;
    /** 握手完成保护时长。 */
    private static final long HANDSHAKE_TIMEOUT_SECONDS = 10;
    /** RFC 6455 规定的 WebSocket GUID。 */
    private static final String WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

    private static EventLoopGroup group;
    private static volatile Channel channel;
    /** 前置链路可用（hello ok 后为 true）。 */
    private static volatile boolean connected;
    /** 自动重连开关：大脑断线归零后关闭，大脑重连时恢复。 */
    private static volatile boolean autoReconnect = true;
    private static boolean reconnectScheduled;
    private static ScheduledFuture<?> pingTask;
    private static long nextReqId;
    /** 在途转发请求：前置侧 id → 待响应（仅在 eventLoop 线程访问）。 */
    private static final Map<Long, Pending> pending = new HashMap<>();

    private PrereqClient() {
    }

    /** 转发请求回调（在 PrereqClient eventLoop 线程执行；写大脑 channel 线程安全）。 */
    public interface Callback {
        void onResult(JsonElement result);

        void onError(int code, String message);
    }

    /** 启动转发链路（幂等）：连接前置 + 失败自动重试。 */
    public static void start() {
        if (group != null) {
            return;
        }
        group = new NioEventLoopGroup(1, new DefaultThreadFactory("tower-prereq", true));
        connect();
        // 兜底：主菜单直接退出时无 ForgeShutdownEvent
        Runtime.getRuntime().addShutdownHook(new Thread(PrereqClient::shutdown, "tower-prereq-shutdown"));
    }

    /** 关闭链路（幂等）。 */
    public static void shutdown() {
        autoReconnect = false;
        if (channel != null) {
            channel.close();
            channel = null;
        }
        if (pingTask != null) {
            pingTask.cancel(false);
            pingTask = null;
        }
        pending.clear();
        if (group != null) {
            group.shutdownGracefully();
            group = null;
        }
    }

    /** 前置链路是否可用（hello 宣告 prereq 状态；转发动作 501 判断）。 */
    public static boolean isConnected() {
        return connected;
    }

    /**
     * 大脑重连时调用：恢复自动重连并立即尝试连接（若当前未连接）。
     */
    public static void ensureConnected() {
        if (group == null) {
            return;
        }
        group.next().execute(() -> {
            autoReconnect = true;
            connectOnLoop();
        });
    }

    /**
     * 发送转发请求（异步）。返回 false 表示前置链路不可用（调用方回 501）；
     * 返回 true 表示已入队，响应经 {@link Callback} 回调。
     */
    public static boolean sendRequest(String action, JsonObject params, Callback cb) {
        Channel ch = channel;
        if (ch == null || !connected || !ch.isActive()) {
            return false;
        }
        ch.eventLoop().execute(() -> {
            if (!ch.isActive()) {
                return; // 竞态兜底：连接刚断开
            }
            final long id = ++nextReqId;
            JsonObject req = new JsonObject();
            req.addProperty("type", "request");
            req.addProperty("id", id);
            req.addProperty("action", action);
            req.add("params", params == null ? new JsonObject() : params);
            Pending p = new Pending(cb);
            pending.put(id, p);
            p.timeout = ch.eventLoop().schedule(() -> {
                pending.remove(id);
                cb.onError(MessageCodec.ERR_INTERNAL, "前置响应超时");
            }, REQUEST_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            ch.writeAndFlush(req.toString());
        });
        return true;
    }

    /** 大脑断线清理（协议 §2.4）：主动归零序列 → 断开前置 → 关闭自动重连。 */
    public static void zeroAndDisconnect() {
        if (group == null) {
            return;
        }
        group.next().execute(PrereqClient::zeroAndDisconnectOnLoop);
    }

    private static void zeroAndDisconnectOnLoop() {
        autoReconnect = false;
        Channel ch = channel;
        if (ch != null && ch.isActive() && connected) {
            // 归零序列（协议 §2.4 ①）：前置断线归零不覆盖 attack hold，由 Tower 主动补发
            String[][] zeroSeq = {
                    {"attack", "{\"mode\":\"release\"}"},
                    {"move", "{}"},
                    {"jump", "{\"value\":false}"},
                    {"sneak", "{\"value\":false}"},
                    {"look_at", "{}"},
                    {"sprint", "{\"value\":false}"},
                    {"swim", "{\"value\":false}"},
                    {"fly", "{\"value\":false}"},
                    {"fall_fly", "{\"value\":false}"},
            };
            for (int i = 0; i < zeroSeq.length; i++) {
                JsonObject req = new JsonObject();
                req.addProperty("type", "request");
                req.addProperty("id", ++nextReqId);
                req.addProperty("action", zeroSeq[i][0]);
                req.add("params", JsonParser.parseString(zeroSeq[i][1]).getAsJsonObject());
                if (i == zeroSeq.length - 1) {
                    // 最后一条 flush 完成后关闭：保证归零请求先于关闭帧到达前置
                    ch.writeAndFlush(req.toString()).addListener(ChannelFutureListener.CLOSE);
                } else {
                    ch.writeAndFlush(req.toString());
                }
            }
            LOGGER.info("Tower: 大脑断线——已发送归零序列并断开前置（协议 §2.4 双保险）");
        } else if (ch != null && ch.isActive()) {
            ch.close();
        }
        cancelPending();
        EventRelay.clear();
    }

    /** 触发一次连接尝试（封送 eventLoop，防并发竞态）。 */
    private static void connect() {
        if (group == null) {
            return;
        }
        group.next().execute(PrereqClient::connectOnLoop);
    }

    private static void connectOnLoop() {
        if (!autoReconnect || (channel != null && channel.isActive())) {
            return;
        }
        PrereqConfig.ConnectionInfo info = PrereqConfig.load();
        if (info == null) {
            LOGGER.warn("Tower: 前置配置未找到（config/keyboard.json），{}s 后重试", RECONNECT_DELAY_SECONDS);
            scheduleReconnect();
            return;
        }
        Bootstrap bootstrap = new Bootstrap()
                .group(group)
                .channel(NioSocketChannel.class)
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 5000)
                .handler(new ChannelInitializer<SocketChannel>() {
                    @Override
                    protected void initChannel(SocketChannel ch) {
                        ch.pipeline().addLast(new ClientHandshakeDecoder(info.token, info.port));
                    }
                });
        bootstrap.connect("127.0.0.1", info.port).addListener(future -> {
            if (!future.isSuccess()) {
                LOGGER.warn("Tower: 连接前置失败（ws://127.0.0.1:{}）: {}", info.port, future.cause().getMessage());
                scheduleReconnect();
            }
        });
    }

    private static void scheduleReconnect() {
        if (reconnectScheduled) {
            return;
        }
        reconnectScheduled = true;
        group.next().schedule(() -> {
            reconnectScheduled = false;
            connectOnLoop();
        }, RECONNECT_DELAY_SECONDS, TimeUnit.SECONDS);
    }

    /** 取消全部在途请求的定时器并清空（断线时大脑已断开，无需回响应）。 */
    private static void cancelPending() {
        for (Pending p : pending.values()) {
            if (p.timeout != null) {
                p.timeout.cancel(false);
            }
        }
        pending.clear();
    }

    /** 在途请求全部失败（前置意外断开，大脑仍在：按 401 回）。 */
    private static void failPending(int code, String message) {
        for (Pending p : pending.values()) {
            if (p.timeout != null) {
                p.timeout.cancel(false);
            }
            p.cb.onError(code, message);
        }
        pending.clear();
    }

    private static void startPing(ChannelHandlerContext ctx) {
        pingTask = ctx.executor().scheduleAtFixedRate(
                () -> ctx.writeAndFlush("{\"type\":\"ping\"}"),
                PING_INTERVAL_SECONDS, PING_INTERVAL_SECONDS, TimeUnit.SECONDS);
    }

    private static byte[] sha1(String input) {
        try {
            return MessageDigest.getInstance("SHA-1").digest(input.getBytes(StandardCharsets.US_ASCII));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-1 不可用", e);
        }
    }

    /** 查找 {@code \r\n\r\n} 在 buffer 中的字节偏移；未找到返回 -1。 */
    private static int indexOfHeaderEnd(ByteBuf in) {
        int start = in.readerIndex();
        int idx = in.indexOf(start, in.writerIndex(), (byte) '\r');
        while (idx >= 0 && idx + 3 < in.writerIndex()) {
            if (in.getByte(idx + 1) == '\n' && in.getByte(idx + 2) == '\r' && in.getByte(idx + 3) == '\n') {
                return idx - start + 4;
            }
            int next = in.indexOf(idx + 1, in.writerIndex(), (byte) '\r');
            if (next <= idx) {
                break;
            }
            idx = next;
        }
        return -1;
    }

    /** 客户端侧 HTTP 握手（RFC 6455 §4）：发升级请求，校验 101 + Sec-WebSocket-Accept。 */
    private static final class ClientHandshakeDecoder extends ByteToMessageDecoder {
        private final String token;
        private final int port;
        private final String key;
        private final String expectedAccept;
        private ScheduledFuture<?> guard;

        ClientHandshakeDecoder(String token, int port) {
            this.token = token;
            this.port = port;
            byte[] kb = new byte[16];
            ThreadLocalRandom.current().nextBytes(kb);
            this.key = Base64.getEncoder().encodeToString(kb);
            this.expectedAccept = Base64.getEncoder().encodeToString(sha1(key + WS_GUID));
        }

        @Override
        public void channelActive(ChannelHandlerContext ctx) {
            String req = "GET /?token=" + URLEncoder.encode(token, StandardCharsets.UTF_8) + " HTTP/1.1\r\n"
                    + "Host: 127.0.0.1:" + port + "\r\n"
                    + "Upgrade: websocket\r\n"
                    + "Connection: Upgrade\r\n"
                    + "Sec-WebSocket-Key: " + key + "\r\n"
                    + "Sec-WebSocket-Version: 13\r\n"
                    + "\r\n";
            ctx.writeAndFlush(Unpooled.copiedBuffer(req, StandardCharsets.UTF_8));
            // 握手完成保护：超时未完成则关闭（错误端口连到非 WS 服务时避免悬挂）
            guard = ctx.executor().schedule(() -> {
                if (ctx.channel().isActive()) {
                    LOGGER.warn("Tower: 前置握手超时（{}s），断开", HANDSHAKE_TIMEOUT_SECONDS);
                    ctx.close();
                }
            }, HANDSHAKE_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        }

        @Override
        protected void decode(ChannelHandlerContext ctx, ByteBuf in, List<Object> out) {
            int headerEnd = indexOfHeaderEnd(in);
            if (headerEnd < 0) {
                if (in.readableBytes() > MAX_HANDSHAKE_BYTES) {
                    ctx.close();
                }
                return;
            }
            String response = in.readCharSequence(headerEnd, StandardCharsets.UTF_8).toString();
            if (in.isReadable()) {
                out.add(in.readBytes(in.readableBytes()));
            }

            String[] lines = response.split("\r\n");
            String[] statusLine = lines[0].split(" ");
            if (statusLine.length < 2 || !"101".equals(statusLine[1])) {
                LOGGER.warn("Tower: 前置握手失败（HTTP {}，非前置服务？）",
                        statusLine.length >= 2 ? statusLine[1] : "?");
                ctx.close();
                return;
            }
            String accept = null;
            for (int i = 1; i < lines.length; i++) {
                int colon = lines[i].indexOf(':');
                if (colon <= 0) {
                    continue;
                }
                if ("sec-websocket-accept".equals(lines[i].substring(0, colon).trim().toLowerCase())) {
                    accept = lines[i].substring(colon + 1).trim();
                }
            }
            if (accept == null || !accept.equals(expectedAccept)) {
                LOGGER.warn("Tower: 前置握手 Sec-WebSocket-Accept 不匹配（非前置服务？），断开");
                ctx.close();
                return;
            }
            if (guard != null) {
                guard.cancel(false);
            }
            ctx.pipeline().addLast(new WsClientFrameDecoder(), new WsClientFrameEncoder(), new SessionHandler());
            ctx.pipeline().remove(this);
            // 重传播 channelActive：会话层加入 pipeline 前已触发过（当时无会话层）
            ctx.pipeline().fireChannelActive();
            LOGGER.info("Tower: 前置握手成功，等待 hello");
        }
    }

    /** 会话层：hello 校验 / 响应关联 / 事件中继 / 心跳 / 断线处理。 */
    private static final class SessionHandler extends SimpleChannelInboundHandler<String> {
        @Override
        protected void channelRead0(ChannelHandlerContext ctx, String message) {
            JsonObject obj;
            try {
                obj = JsonParser.parseString(message).getAsJsonObject();
            } catch (Exception e) {
                LOGGER.debug("Tower: 前置消息非 JSON，忽略: {}", message);
                return;
            }
            String type = obj.get("type") == null ? "" : obj.get("type").getAsString();
            if ("hello".equals(type)) {
                handleHello(ctx, obj);
            } else if ("event".equals(type)) {
                EventRelay.relay(obj);
            } else if (obj.get("id") != null && obj.get("id").isJsonPrimitive()
                    && obj.get("id").getAsJsonPrimitive().isNumber()) {
                handleResponse(obj);
            } else if ("pong".equals(type)) {
                // 心跳应答，忽略
            } else {
                LOGGER.debug("Tower: 忽略前置消息: {}", message);
            }
        }

        private void handleHello(ChannelHandlerContext ctx, JsonObject hello) {
            boolean ok = hello.get("ok") != null && hello.get("ok").getAsBoolean();
            if (!ok) {
                String error = hello.get("error") == null ? "?" : hello.get("error").getAsString();
                LOGGER.error("Tower: 前置鉴权失败（{}，前置 token 已变更？）——{}s 后重试", error, RECONNECT_DELAY_SECONDS);
                ctx.close();
                return;
            }
            int protocol = hello.get("protocol") == null ? 0 : hello.get("protocol").getAsInt();
            if (protocol != 2) {
                LOGGER.warn("Tower: 前置协议版本 {} 与预期 2 不一致（D3 前置协议冻结，请注意）", protocol);
            }
            channel = ctx.channel();
            connected = true;
            startPing(ctx);
            LOGGER.info("Tower: 已连接前置 mod（protocol={}，转发链路就绪）", protocol);
        }

        private void handleResponse(JsonObject resp) {
            long id = resp.get("id").getAsLong();
            Pending p = pending.remove(id);
            if (p == null) {
                return; // 归零序列等 fire-and-forget 请求的响应
            }
            if (p.timeout != null) {
                p.timeout.cancel(false);
            }
            boolean ok = resp.get("ok") != null && resp.get("ok").getAsBoolean();
            if (ok) {
                JsonElement result = resp.get("result");
                p.cb.onResult(result == null || result.isJsonNull() ? new JsonObject() : result);
            } else {
                JsonElement err = resp.get("error");
                int code = 0;
                String message = "未知错误";
                if (err != null && err.isJsonObject()) {
                    JsonObject e = err.getAsJsonObject();
                    if (e.get("code") != null) {
                        code = e.get("code").getAsInt();
                    }
                    if (e.get("message") != null) {
                        message = e.get("message").getAsString();
                    }
                }
                p.cb.onError(code, message);
            }
        }

        @Override
        public void channelInactive(ChannelHandlerContext ctx) {
            if (channel == ctx.channel()) {
                channel = null;
            }
            connected = false;
            if (pingTask != null) {
                pingTask.cancel(false);
                pingTask = null;
            }
            failPending(MessageCodec.ERR_INTERNAL, "前置链路断开");
            EventRelay.clear();
            if (autoReconnect) {
                LOGGER.info("Tower: 前置链路断开，{}s 后重连", RECONNECT_DELAY_SECONDS);
                scheduleReconnect();
            } else {
                LOGGER.info("Tower: 前置链路已断开（大脑断线归零，等待大脑重连）");
            }
        }

        @Override
        public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
            LOGGER.error("Tower: 前置连接异常，断开", cause);
            ctx.close();
        }
    }

    /** 在途转发请求记录。 */
    private static final class Pending {
        final Callback cb;
        ScheduledFuture<?> timeout;

        Pending(Callback cb) {
            this.cb = cb;
        }
    }
}
