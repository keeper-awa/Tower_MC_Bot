package com.tower.net;

import com.tower.config.ConfigManager;
import io.netty.channel.Channel;
import io.netty.channel.ChannelFutureListener;

/**
 * 鉴权与单连接管理。
 *
 * <p>token 校验：与 {@link ConfigManager#getToken()} 比对（协议 §2）。
 * 单连接：同一时刻仅一个客户端（协议 §1），新连接握手鉴权通过后挤掉旧连接。
 *
 * <p>线程安全：所有方法仅在 Netty eventLoop 线程（网络层）调用；
 * 连接引用用 volatile 保证启动/关闭（主线程）的可见性。
 */
public final class AuthManager {
    /** 旧连接被挤掉时使用的关闭码（1000-4999 为应用私有码）。 */
    static final int CLOSE_CODE_REPLACED = 4001;

    private static volatile Channel current;

    private AuthManager() {
    }

    /** 校验连接 URL 中的 token（协议 §2）。 */
    public static boolean verify(String token) {
        String expected = ConfigManager.getToken();
        return expected != null && expected.equals(token);
    }

    /**
     * 注册当前活动连接；若已有其他活动连接，先发送关闭帧挤掉它。
     */
    public static void register(Channel channel) {
        Channel old = current;
        if (old != null && old != channel && old.isActive()) {
            old.writeAndFlush(WsFrameEncoder.closeFrame(old.alloc(), CLOSE_CODE_REPLACED))
                    .addListener(ChannelFutureListener.CLOSE);
        }
        current = channel;
    }

    /** 连接关闭时注销；仅当它就是当前连接时清除，避免挤掉旧连接时误清新连接。 */
    public static void unregister(Channel channel) {
        if (current == channel) {
            current = null;
        }
    }

    /** 当前活动连接（无连接时为 null）；事件推送用（线程安全，volatile 读）。 */
    public static Channel getCurrent() {
        return current;
    }
}
