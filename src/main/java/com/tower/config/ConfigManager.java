package com.tower.config;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonParseException;
import com.mojang.logging.LogUtils;
import org.slf4j.Logger;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.HexFormat;

/**
 * 配置管理（仅客户端使用）。
 *
 * <p>配置文件位于游戏 {@code config/tower.json}：
 * <ul>
 *   <li>port —— Tower WS 服务器监听端口（默认 24778，协议 §1）</li>
 *   <li>token —— 连接鉴权 token，首启随机生成（32 位十六进制）；删除配置文件可重新生成</li>
 * </ul>
 *
 * <p>token 仅在生成时打印到日志一次；泄露后删除配置文件重启游戏即可更换。
 * 后续里程碑在此扩展：前置 mod 的端口/token 由 M1.3 直接读取
 * 前置的 {@code config/keyboard.json}，本配置不冗余存储。
 */
public final class ConfigManager {
    private static final Logger LOGGER = LogUtils.getLogger();
    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
    private static final String FILE_NAME = "tower.json";
    private static final int DEFAULT_PORT = 24778;
    private static final SecureRandom RANDOM = new SecureRandom();

    private static ConfigData data;

    private ConfigManager() {
    }

    /** 加载或创建配置。仅在客户端调用。 */
    public static void init(Path configDir) {
        Path file = configDir.resolve(FILE_NAME);
        if (Files.exists(file)) {
            data = load(file);
        } else {
            data = new ConfigData(DEFAULT_PORT, generateToken());
            save(file, data);
            // token 仅在新生成时打印；安全提示见协议文档 §11
            LOGGER.warn("Tower: 已生成新 token（勿泄露，泄露可删除配置文件重启重新生成）：{}  配置文件：{}",
                    data.token, file.toAbsolutePath());
        }
        LOGGER.info("Tower: 配置已加载 port={}", data.port);
    }

    private static ConfigData load(Path file) {
        try {
            ConfigData d = GSON.fromJson(Files.readString(file, StandardCharsets.UTF_8), ConfigData.class);
            if (isValid(d)) {
                return d;
            }
            LOGGER.error("Tower: 配置文件内容非法，将重新生成: {}", file);
        } catch (IOException | JsonParseException e) {
            LOGGER.error("Tower: 读取配置失败，将重新生成: {}", file, e);
        }
        ConfigData d = new ConfigData(DEFAULT_PORT, generateToken());
        save(file, d);
        LOGGER.warn("Tower: 已生成新 token（原配置损坏或缺失）：{}", d.token);
        return d;
    }

    private static boolean isValid(ConfigData d) {
        return d != null
                && d.port >= 1 && d.port <= 65535
                && d.token != null && !d.token.isBlank();
    }

    private static void save(Path file, ConfigData d) {
        try {
            Files.createDirectories(file.getParent());
            Files.writeString(file, GSON.toJson(d), StandardCharsets.UTF_8);
        } catch (IOException e) {
            LOGGER.error("Tower: 写入配置文件失败: {}", file, e);
        }
    }

    private static String generateToken() {
        byte[] bytes = new byte[16];
        RANDOM.nextBytes(bytes);
        return HexFormat.of().formatHex(bytes);
    }

    public static int getPort() {
        return data == null ? DEFAULT_PORT : data.port;
    }

    public static String getToken() {
        return data == null ? null : data.token;
    }

    /** Gson 序列化/反序列化的数据类；字段为包私有。 */
    private static final class ConfigData {
        int port;
        String token;

        ConfigData(int port, String token) {
            this.port = port;
            this.token = token;
        }
    }
}
