package com.tower.config;

import com.google.gson.Gson;
import com.mojang.logging.LogUtils;
import org.slf4j.Logger;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * 前置 mod 连接配置读取（决策 A：直接读前置的 {@code config/keyboard.json}，零配置）。
 *
 * <p>两个 mod 部署在同一个 mods 文件夹（同一 config 目录），读取
 * {@code {port, token}} 两字段（前置协议 §2 鉴权）。不缓存——每次连接/重连时
 * 重新读取，前置 token 轮换（删配置重启）后 Tower 自动跟随。
 *
 * <p>文件缺失/损坏返回 {@code null}：调用方（PrereqClient）按 501 处理并重试
 * （前置可能在 Tower 之后才生成配置）。
 */
public final class PrereqConfig {
    private static final Logger LOGGER = LogUtils.getLogger();
    private static final Gson GSON = new Gson();
    private static final String FILE_NAME = "keyboard.json";

    private static Path configPath;

    private PrereqConfig() {
    }

    /** 初始化配置文件路径（游戏 config 目录）。 */
    public static void init(Path configDir) {
        configPath = configDir.resolve(FILE_NAME);
    }

    /** 读取前置连接信息；文件缺失/损坏返回 null。 */
    public static ConnectionInfo load() {
        if (configPath == null || !Files.exists(configPath)) {
            return null;
        }
        try {
            Data d = GSON.fromJson(Files.readString(configPath, StandardCharsets.UTF_8), Data.class);
            if (d != null && d.port >= 1 && d.port <= 65535
                    && d.token != null && !d.token.isBlank()) {
                return new ConnectionInfo(d.port, d.token);
            }
            LOGGER.error("Tower: 前置配置内容非法（应为 {{port, token}}）: {}", configPath);
        } catch (Exception e) {
            LOGGER.error("Tower: 读取前置配置失败: {}", configPath, e);
        }
        return null;
    }

    /** 前置连接信息（port + token）。 */
    public static final class ConnectionInfo {
        public final int port;
        public final String token;

        public ConnectionInfo(int port, String token) {
            this.port = port;
            this.token = token;
        }
    }

    /** Gson 反序列化数据类。 */
    private static final class Data {
        int port;
        String token;
    }
}
