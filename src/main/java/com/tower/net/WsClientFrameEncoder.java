package com.tower.net;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.ByteBufAllocator;
import io.netty.channel.ChannelHandlerContext;
import io.netty.handler.codec.MessageToMessageEncoder;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.ThreadLocalRandom;

/**
 * 出站（客户端侧）：String → WebSocket text 帧（RFC 6455）。
 *
 * <p>与 {@link WsFrameEncoder}（服务端侧）的差异：**客户端→服务端帧必须掩码**
 * （RFC 6455 §5.1），本类对所有出站帧（含 close/pong 控制帧）加 4 字节掩码。
 *
 * <p>线程模型：仅在 PrereqClient 的 eventLoop 线程运行。
 */
public class WsClientFrameEncoder extends MessageToMessageEncoder<String> {
    private static final byte OP_TEXT = 0x1;
    private static final byte OP_CLOSE = 0x8;
    private static final byte OP_PONG = 0xA;

    @Override
    protected void encode(ChannelHandlerContext ctx, String msg, List<Object> out) {
        out.add(textFrame(ctx.alloc(), msg.getBytes(StandardCharsets.UTF_8)));
    }

    /** 构造掩码 text 帧（FIN=1, opcode=1）。 */
    static ByteBuf textFrame(ByteBufAllocator alloc, byte[] payload) {
        ByteBuf buf = alloc.buffer(14 + payload.length);
        buf.writeByte(0x80 | OP_TEXT);
        writeMaskedLength(buf, payload.length);
        writeMasked(buf, payload);
        return buf;
    }

    /** 构造掩码 close 帧（FIN=1, opcode=8），携带状态码（1000-4999）。 */
    public static ByteBuf closeFrame(ByteBufAllocator alloc, int code) {
        ByteBuf buf = alloc.buffer(10);
        buf.writeByte(0x80 | OP_CLOSE);
        buf.writeByte(0x80 | 2);
        writeMasked(buf, new byte[]{(byte) (code >> 8), (byte) code});
        return buf;
    }

    /** 构造掩码 pong 帧（FIN=1, opcode=10），回显 ping 的 payload。 */
    static ByteBuf pongFrame(ByteBufAllocator alloc, byte[] payload) {
        ByteBuf buf = alloc.buffer(14 + payload.length);
        buf.writeByte(0x80 | OP_PONG);
        writeMaskedLength(buf, payload.length);
        writeMasked(buf, payload);
        return buf;
    }

    /**
     * 写入带掩码位的长度（RFC 6455 §5.1：客户端→服务器帧必须掩码，
     * 第二字节最高位 MASK=1 —— 缺失时对端按协议违规断开 close 1002）。
     */
    private static void writeMaskedLength(ByteBuf buf, int length) {
        if (length < 126) {
            buf.writeByte(0x80 | length);
        } else if (length <= 0xFFFF) {
            buf.writeByte(0x80 | 126);
            buf.writeShort(length);
        } else {
            buf.writeByte(0x80 | 127);
            buf.writeLong(length);
        }
    }

    /** 写入 4 字节掩码并异或整个 payload。 */
    private static void writeMasked(ByteBuf buf, byte[] payload) {
        int mask = ThreadLocalRandom.current().nextInt();
        buf.writeInt(mask);
        for (int i = 0; i < payload.length; i++) {
            buf.writeByte(payload[i] ^ ((mask >>> (8 * (3 - (i & 3)))) & 0xFF));
        }
    }
}
