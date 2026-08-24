package com.tower.control;

import com.google.gson.JsonObject;
import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.Util;
import net.minecraft.client.player.LocalPlayer;
import org.slf4j.Logger;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Stream;

/**
 * screenshot（协议 §5.4）：走原版截屏管线，PNG 保存到
 * {@code <游戏目录>/screenshots/tower/} 单独子目录（与原版截图互不干扰）。
 *
 * <p>**溢出清理**：目录内截图超过 {@link #MAX_FILES}（10）张时删除最早的一张
 * （保持 ≤10，防无限占盘）。
 *
 * <p>保存为异步（ioPool），响应立即返回 path+尺寸——协议 §5.4 注：大脑读到路径后
 * 需带重试等待文件出现（一般 &lt; 2s）。
 */
public final class ScreenshotAction extends NativeAction {
    private static final Logger LOGGER = LogUtils.getLogger();
    /** 截图目录保留上限（超过清理最早一张）。 */
    private static final int MAX_FILES = 10;
    /** 截图文件名前缀（清理识别用）。 */
    private static final String FILE_PREFIX = "tower_";
    /** 文件名序号：时间戳精度仅到秒，连拍同秒需序号保证唯一（防覆盖）。 */
    private static final AtomicInteger SEQ = new AtomicInteger();

    @Override
    protected JsonObject execute(LocalPlayer player, ActionContext ctx, JsonObject params) {
        Minecraft mc = Minecraft.getInstance();
        // 读帧缓冲（主线程即渲染线程，GL 上下文就绪）
        var image = Screenshot.takeScreenshot(mc.getMainRenderTarget());
        image.flipY();
        int width = image.getWidth();
        int height = image.getHeight();

        Path towerDir = mc.gameDirectory.toPath()
                .resolve(Screenshot.SCREENSHOT_DIR).resolve("tower");
        Path file = towerDir.resolve(FILE_PREFIX + Util.getFilenameFormattedDateTime()
                + "_" + SEQ.incrementAndGet() + ".png");

        // 异步保存 + 溢出清理（响应立即返回；大脑带重试等文件出现）
        Util.ioPool().execute(() -> {
            try {
                Files.createDirectories(towerDir);
                image.writeToFile(file);
                prune(towerDir);
                LOGGER.info("Tower: 截图已保存 {}", file);
            } catch (IOException e) {
                LOGGER.error("Tower: 截图保存失败", e);
            } finally {
                image.close();
            }
        });

        JsonObject result = new JsonObject();
        result.addProperty("path", file.toAbsolutePath().toString());
        result.addProperty("width", width);
        result.addProperty("height", height);
        return result;
    }

    /** 溢出清理：目录内 {@code tower_*.png} 超过上限时删除最早的，保持 ≤ 上限。 */
    private static void prune(Path dir) {
        try (Stream<Path> stream = Files.list(dir)) {
            List<Path> files = stream
                    .filter(p -> p.getFileName().toString().startsWith(FILE_PREFIX))
                    .sorted(Comparator.comparingLong(p -> p.toFile().lastModified()))
                    .toList();
            for (int i = 0; i < files.size() - MAX_FILES; i++) {
                Files.deleteIfExists(files.get(i));
                LOGGER.info("Tower: 截图溢出清理（保留 {} 张）: {}", MAX_FILES, files.get(i).getFileName());
            }
        } catch (IOException e) {
            LOGGER.warn("Tower: 截图目录清理失败", e);
        }
    }
}
