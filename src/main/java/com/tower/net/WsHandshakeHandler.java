package com.tower.net;

import com.mojang.logging.LogUtils;
import io.netty.buffer.ByteBuf;
import io.netty.channel.ChannelHandlerContext;
import io.netty.handler.codec.ByteToMessageDecoder;
import io.netty.util.AttributeKey;
import org.slf4j.Logger;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.List;

/**
 * HTTP 握手（RFC 6455 §4）：解析 GET 请求行与 headers，计算
 * {@code Sec-WebSocket-Accept} 并返回 101；失败返回 HTTP 错误码并断开。
 *
 * <p>token 取自连接 URL 的 {@code ?token=xxx}（协议 §2），提取后存入
 * channel attribute，由 {@link WsSessionHandler} 在会话建立时校验——鉴权失败
 * 仍完成 WS 握手，随后发送 {@code hello{ok:false,error:auth_failed}} 再断开
 * （协议 §2，让客户端能收到明确的失败原因而非连接错误）。
 *
 * <p>握手成功后从 pipeline 移除自身，并接入帧编解码与会话层。
 * 线程模型：仅在 Netty eventLoop 线程运行。
 */
public class WsHandshakeHandler extends ByteToMessageDecoder {
    private static final Logger LOGGER = LogUtils.getLogger();
    /** 握手请求大小上限（防恶意大请求，16KB 远大于正常握手）。 */
    private static final int MAX_HANDSHAKE_BYTES = 16 * 1024;
    /** RFC 6455 规定的 WebSocket GUID。 */
    private static final String WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

    /** 保存连接 URL 中的 token，供会话层鉴权。 */
    static final AttributeKey<String> TOKEN_KEY = AttributeKey.valueOf("tower-token");

    @Override
    protected void decode(ChannelHandlerContext ctx, ByteBuf in, List<Object> out) {
        int headerEnd = indexOfHeaderEnd(in);
        if (headerEnd < 0) {
            if (in.readableBytes() > MAX_HANDSHAKE_BYTES) {
                httpError(ctx, 431);
            }
            return; // 请求头未完整，等待更多数据
        }
        String request = in.readCharSequence(headerEnd, StandardCharsets.UTF_8).toString();
        // 请求头之后的字节（客户端理论上等 101 后才发帧，稳妥起见透传给帧层）
        if (in.isReadable()) {
            out.add(in.readBytes(in.readableBytes()));
        }

        HttpRequest req = parse(request);
        if (req == null || !"GET".equalsIgnoreCase(req.method)) {
            httpError(ctx, 405);
            return;
        }
        if (!"websocket".equalsIgnoreCase(req.upgrade) || req.secWebSocketKey == null) {
            httpError(ctx, 400);
            return;
        }

        String accept = Base64.getEncoder().encodeToString(sha1(req.secWebSocketKey + WS_GUID));
        ByteBuf resp = ctx.alloc().buffer(128);
        resp.writeCharSequence("HTTP/1.1 101 Switching Protocols\r\n"
                + "Upgrade: websocket\r\n"
                + "Connection: Upgrade\r\n"
                + "Sec-WebSocket-Accept: " + accept + "\r\n"
                + "\r\n", StandardCharsets.UTF_8);
        ctx.writeAndFlush(resp);

        ctx.channel().attr(TOKEN_KEY).set(req.token);
        ctx.pipeline().addLast(
                new WsFrameDecoder(),
                new WsFrameEncoder(),
                new WsSessionHandler());
        ctx.pipeline().remove(this);
        // channelActive 在连接建立时已触发过（当时会话层尚未加入 pipeline），
        // 此处重新传播，让 WsSessionHandler 完成鉴权并发送 hello（协议 §2）
        ctx.pipeline().fireChannelActive();
        LOGGER.info("Tower: WebSocket 握手成功（token 鉴权将在会话层完成）");
    }

    /** 解析请求行与 headers；格式非法返回 null。 */
    private static HttpRequest parse(String request) {
        String[] lines = request.split("\r\n");
        if (lines.length == 0) {
            return null;
        }
        String[] requestLine = lines[0].split(" ");
        if (requestLine.length < 3) {
            return null;
        }
        HttpRequest r = new HttpRequest();
        r.method = requestLine[0];
        String path = requestLine[1];
        int q = path.indexOf('?');
        if (q >= 0) {
            for (String pair : path.substring(q + 1).split("&")) {
                int eq = pair.indexOf('=');
                if (eq > 0 && "token".equals(pair.substring(0, eq))) {
                    r.token = URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8);
                }
            }
        }
        for (int i = 1; i < lines.length; i++) {
            int colon = lines[i].indexOf(':');
            if (colon <= 0) {
                continue;
            }
            String name = lines[i].substring(0, colon).trim().toLowerCase();
            String value = lines[i].substring(colon + 1).trim();
            switch (name) {
                case "upgrade":
                    r.upgrade = value.toLowerCase();
                    break;
                case "sec-websocket-key":
                    r.secWebSocketKey = value;
                    break;
                default:
                    break;
            }
        }
        return r;
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

    private static byte[] sha1(String input) {
        try {
            return MessageDigest.getInstance("SHA-1").digest(input.getBytes(StandardCharsets.US_ASCII));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-1 不可用", e); // JDK 必含
        }
    }

    private static void httpError(ChannelHandlerContext ctx, int code) {
        String reason;
        switch (code) {
            case 400:
                reason = "Bad Request";
                break;
            case 405:
                reason = "Method Not Allowed";
                break;
            case 431:
                reason = "Request Header Fields Too Large";
                break;
            default:
                reason = "Error";
                break;
        }
        ByteBuf resp = ctx.alloc().buffer(64);
        resp.writeCharSequence("HTTP/1.1 " + code + " " + reason + "\r\nContent-Length: 0\r\n\r\n",
                StandardCharsets.UTF_8);
        ctx.writeAndFlush(resp);
        ctx.close();
    }

    /** 握手请求的解析结果。 */
    private static final class HttpRequest {
        String method;
        String token;
        String upgrade;
        String secWebSocketKey;
    }
}
