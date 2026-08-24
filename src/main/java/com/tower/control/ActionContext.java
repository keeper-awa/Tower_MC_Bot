package com.tower.control;

import io.netty.channel.Channel;

/**
 * 动作执行上下文：发起请求的大脑连接与请求 id（异步响应用）。
 *
 * <p>M1.3 转发动作在 Netty 线程执行（纯网络操作，不触碰游戏对象）；
 * M2+ 原生动作需访问游戏对象时，在处理器内自行封送游戏主线程。
 */
public final class ActionContext {
    /** 大脑连接（响应写回目标）。 */
    public final Channel channel;
    /** 大脑请求 id（响应原样带回，协议 §4）。 */
    public final long id;

    public ActionContext(Channel channel, long id) {
        this.channel = channel;
        this.id = id;
    }
}
