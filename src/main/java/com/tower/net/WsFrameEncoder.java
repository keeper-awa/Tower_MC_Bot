package com.tower.net;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.ByteBufAllocator;
import io.netty.channel.ChannelHandlerContext;
import io.netty.handler.codec.MessageToMessageEncoder;

import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * 出站：String → WebSocket text 帧（RFC 6455，服务端→客户端帧不掩码）。
 *
 * <p>同时提供 close / pong 控制帧的构造方法（协议层控制用）。
 */
public class WsFrameEncoder extends MessageToMessageEncoder<String> {
    private static final byte OP_TEXT = 0x1;
    private static final byte OP_CLOSE = 0x8;
    private static final byte OP_PONG = 0xA;

    @Override
    protected void encode(ChannelHandlerContext ctx, String msg, List<Object> out) {
        out.add(textFrame(ctx.alloc(), msg.getBytes(StandardCharsets.UTF_8)));
    }

    /** 构造 text 帧（FIN=1, opcode=1）。 */
    static ByteBuf textFrame(ByteBufAllocator alloc, byte[] payload) {
        ByteBuf buf = alloc.buffer(10 + payload.length);
        buf.writeByte(0x80 | OP_TEXT);
        writeLength(buf, payload.length);
        buf.writeBytes(payload);
        return buf;
    }

    /** 构造 close 帧（FIN=1, opcode=8），携带状态码（1000-4999）。 */
    public static ByteBuf closeFrame(ByteBufAllocator alloc, int code) {
        ByteBuf buf = alloc.buffer(4);
        buf.writeByte(0x80 | OP_CLOSE);
        buf.writeByte(2);
        buf.writeShort(code);
        return buf;
    }

    /** 构造 pong 帧（FIN=1, opcode=10），回显 ping 的 payload。 */
    static ByteBuf pongFrame(ByteBufAllocator alloc, byte[] payload) {
        ByteBuf buf = alloc.buffer(10 + payload.length);
        buf.writeByte(0x80 | OP_PONG);
        writeLength(buf, payload.length);
        buf.writeBytes(payload);
        return buf;
    }

    private static void writeLength(ByteBuf buf, int length) {
        if (length < 126) {
            buf.writeByte(length);
        } else if (length <= 0xFFFF) {
            buf.writeByte(126);
            buf.writeShort(length);
        } else {
            buf.writeByte(127);
            buf.writeLong(length);
        }
    }
}
