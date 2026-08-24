package com.tower.net;

import io.netty.buffer.ByteBuf;
import io.netty.channel.ChannelHandlerContext;
import io.netty.handler.codec.ByteToMessageDecoder;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * 入站（客户端侧）：WebSocket 帧解码（RFC 6455）→ text 消息 String。
 *
 * <p>与 {@link WsFrameDecoder}（服务端侧）的差异：**服务端→客户端帧不掩码**
 * （RFC 6455 §5.1），故不做掩码要求；其余规则一致：
 * <ul>
 *   <li>仅支持单帧消息（FIN=1）；分片帧拒绝 close(1003)</li>
 *   <li>单帧上限 64KB（协议 §1），超出 close(1009)</li>
 *   <li>text payload 必须是合法 UTF-8，否则 close(1007)</li>
 *   <li>close 帧回显同码并关闭；ping 帧回 pong</li>
 * </ul>
 *
 * <p>线程模型：仅在 PrereqClient 的 eventLoop 线程运行。
 */
public class WsClientFrameDecoder extends ByteToMessageDecoder {
    /** 协议消息大小上限（《Tower协议.md》§1）。 */
    private static final long MAX_FRAME_SIZE = 64 * 1024;

    private static final int OP_CONTINUATION = 0x0;
    private static final int OP_TEXT = 0x1;
    private static final int OP_BINARY = 0x2;
    private static final int OP_CLOSE = 0x8;
    private static final int OP_PING = 0x9;
    private static final int OP_PONG = 0xA;

    @Override
    protected void decode(ChannelHandlerContext ctx, ByteBuf in, List<Object> out) {
        if (in.readableBytes() < 2) {
            return;
        }
        in.markReaderIndex();
        byte b0 = in.readByte();
        byte b1 = in.readByte();
        boolean fin = (b0 & 0x80) != 0;
        int rsv = (b0 >> 4) & 0x07;
        int opcode = b0 & 0x0F;
        long length = b1 & 0x7F;
        if (length == 126) {
            if (in.readableBytes() < 2) {
                in.resetReaderIndex();
                return;
            }
            length = in.readUnsignedShort();
        } else if (length == 127) {
            if (in.readableBytes() < 8) {
                in.resetReaderIndex();
                return;
            }
            length = in.readLong();
        }

        if (rsv != 0 || length < 0) {
            fail(ctx, 1002);
            return;
        }
        if (length > MAX_FRAME_SIZE) {
            fail(ctx, 1009);
            return;
        }
        if (in.readableBytes() < length) {
            in.resetReaderIndex();
            return;
        }
        byte[] payload = new byte[(int) length];
        in.readBytes(payload);

        switch (opcode) {
            case OP_TEXT:
                if (!fin) {
                    fail(ctx, 1003); // 不支持分片
                    return;
                }
                if (!isValidUtf8(payload)) {
                    fail(ctx, 1007);
                    return;
                }
                out.add(new String(payload, StandardCharsets.UTF_8));
                break;
            case OP_CLOSE:
                int code = 1000;
                if (payload.length >= 2) {
                    code = ((payload[0] & 0xFF) << 8) | (payload[1] & 0xFF);
                }
                ctx.writeAndFlush(WsClientFrameEncoder.closeFrame(ctx.alloc(), code));
                ctx.close();
                break;
            case OP_PING:
                ctx.writeAndFlush(WsClientFrameEncoder.pongFrame(ctx.alloc(), payload));
                break;
            case OP_PONG:
                break; // 忽略
            case OP_CONTINUATION:
            case OP_BINARY:
            default:
                fail(ctx, 1003);
                break;
        }
    }

    private static boolean isValidUtf8(byte[] bytes) {
        try {
            StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes));
            return true;
        } catch (CharacterCodingException e) {
            return false;
        }
    }

    private static void fail(ChannelHandlerContext ctx, int closeCode) {
        ctx.writeAndFlush(WsClientFrameEncoder.closeFrame(ctx.alloc(), closeCode));
        ctx.close();
    }
}
