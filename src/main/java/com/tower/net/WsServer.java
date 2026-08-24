package com.tower.net;

import com.mojang.logging.LogUtils;
import com.tower.config.ConfigManager;
import io.netty.bootstrap.ServerBootstrap;
import io.netty.channel.Channel;
import io.netty.channel.ChannelInitializer;
import io.netty.channel.EventLoopGroup;
import io.netty.channel.nio.NioEventLoopGroup;
import io.netty.channel.socket.SocketChannel;
import io.netty.channel.socket.nio.NioServerSocketChannel;
import io.netty.handler.timeout.IdleStateHandler;
import io.netty.util.concurrent.DefaultThreadFactory;
import org.slf4j.Logger;

/**
 * WebSocket 服务器（《Tower协议.md》§1/§2）：监听 127.0.0.1:port（仅本机可连）。
 *
 * <p>连接 pipeline：{@code IdleStateHandler(60s 读空闲) → WsHandshakeHandler}
 * （握手成功后切换为帧编解码 + 会话层）。
 *
 * <p>线程模型：Netty eventLoop 线程仅做网络收发，不触碰游戏对象；
 * 后续里程碑的 Dispatcher 负责将业务封送到游戏主线程（对齐前置 mod）。
 *
 * <p>生命周期：客户端启动时 {@link #start()}；集成服务器关闭（ForgeShutdownEvent）
 * 与 JVM 退出（shutdown hook，覆盖主菜单直接退出场景）时 {@link #shutdown()}。
 * eventLoop 为 daemon 线程，防止游戏退出后残留进程。
 */
public final class WsServer {
    private static final Logger LOGGER = LogUtils.getLogger();
    /** 读空闲 60s 断开（协议 §2.4：客户端心跳 ≤30s）。 */
    private static final int IDLE_SECONDS = 60;

    private static EventLoopGroup group;
    private static Channel serverChannel;
    private static volatile boolean started;

    private WsServer() {
    }

    /** 启动 WS 服务器（幂等）。失败仅记错误日志，不中断游戏。 */
    public static void start() {
        if (started) {
            return;
        }
        int port = ConfigManager.getPort();
        // daemon 线程：游戏从主菜单直接退出时不阻止 JVM 退出
        group = new NioEventLoopGroup(1, new DefaultThreadFactory("tower-ws", true));
        ServerBootstrap bootstrap = new ServerBootstrap()
                .group(group)
                .channel(NioServerSocketChannel.class)
                .childHandler(new ChannelInitializer<SocketChannel>() {
                    @Override
                    protected void initChannel(SocketChannel ch) {
                        ch.pipeline().addLast(new IdleStateHandler(0, 0, IDLE_SECONDS));
                        ch.pipeline().addLast(new WsHandshakeHandler());
                    }
                });
        try {
            serverChannel = bootstrap.bind("127.0.0.1", port).sync().channel();
            started = true;
            LOGGER.info("Tower: WS 服务器已监听 ws://127.0.0.1:{}/（token 见 config/tower.json）", port);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            LOGGER.error("Tower: WS 服务器启动被中断", e);
            shutdown();
        } catch (Exception e) {
            LOGGER.error("Tower: WS 服务器绑定失败（端口 {} 可能被占用）", port, e);
            shutdown();
        }
        // 兜底：主菜单直接退出时无 ForgeShutdownEvent，确保 eventLoop 关闭
        Runtime.getRuntime().addShutdownHook(new Thread(WsServer::shutdown, "tower-ws-shutdown"));
    }

    /** 关闭服务器与事件循环（幂等）。 */
    public static void shutdown() {
        started = false;
        if (serverChannel != null) {
            serverChannel.close();
            serverChannel = null;
        }
        if (group != null) {
            group.shutdownGracefully();
            group = null;
        }
    }
}
