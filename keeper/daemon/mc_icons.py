"""从 Minecraft 游戏 jar 提取物品/方块图标（供 webui 物品栏显示）。

- 源：游戏版本目录下的 *-Forge*.jar（如 1.20.1-Forge_47.4.23.jar）
- 提取：assets/minecraft/textures/item/*.png → mc-icons/item/
         assets/minecraft/textures/block/*.png → mc-icons/block/
- 已存在则跳过（幂等）；找不到 jar 时静默返回空（前端降级为文本）。
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

log = logging.getLogger("keeper.daemon.mc_icons")

# 输出目录：<项目根>/keeper/mc-icons/
ICONS_DIR = Path(__file__).resolve().parent.parent / "mc-icons"
ITEM_DIR = ICONS_DIR / "item"
BLOCK_DIR = ICONS_DIR / "block"


def _find_jar(game_dir: str | Path) -> Path | None:
    """在游戏版本目录找 jar：优先 <目录名>.jar，其次任意 *.jar。"""
    d = Path(game_dir)
    if not d.is_dir():
        return None
    cand = d / f"{d.name}.jar"
    if cand.is_file():
        return cand
    jars = sorted(d.glob("*.jar"))
    return jars[0] if jars else None


def _extract_textures(jar: Path, src_prefix: str, dest: Path) -> int:
    """提取 jar 内 src_prefix/*.png → dest，返回提取数量（跳过已存在）。"""
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with zipfile.ZipFile(jar) as z:
            for name in z.namelist():
                if not (name.startswith(src_prefix) and name.endswith(".png")):
                    continue
                fname = Path(name).name
                target = dest / fname
                if target.exists():
                    continue
                try:
                    target.write_bytes(z.read(name))
                    count += 1
                except Exception:  # noqa: BLE001
                    continue
    except Exception as exc:  # noqa: BLE001
        log.warning("读取 jar 失败: %s", exc)
    return count


def _ensure_hud_icons() -> int:
    """把随项目提交的 HUD 图标（keeper/daemon/hud-icons/）复制到 mc-icons/。

    心形/鸡腿图标从游戏 jar 裁剪坐标易错（1.20.1 布局特殊），改由手工裁剪提交，
    这里只负责在运行时确保存在。返回复制数量。
    """
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    src_dir = Path(__file__).resolve().parent / "hud-icons"
    if not src_dir.is_dir():
        return 0
    count = 0
    for src in sorted(src_dir.glob("*.png")):
        target = ICONS_DIR / src.name
        try:
            if not target.exists() or target.stat().st_mtime != src.stat().st_mtime:
                import shutil

                shutil.copy2(src, target)
                count += 1
        except OSError:
            continue
    if count:
        log.info("已同步 HUD 图标: %d 个", count)
    return count


def ensure_icons(game_dir: str | Path) -> dict[str, int]:
    """提取物品/方块图标 + 同步 HUD 图标到 mc-icons/。返回各分类新增数量。

    幂等：已提取过则 count=0。找不到 jar 返回 {"item": 0, "block": 0, "hud": 0}。
    """
    jar = _find_jar(game_dir)
    item = block = 0
    if jar is not None:
        item = _extract_textures(jar, "assets/minecraft/textures/item/", ITEM_DIR)
        block = _extract_textures(jar, "assets/minecraft/textures/block/", BLOCK_DIR)
    else:
        log.warning("未找到 Minecraft 版本 jar（%s），物品图标不可用", game_dir)
    # HUD 图标（心形/鸡腿）：手工裁剪随项目提交，运行时确保存在
    hud = _ensure_hud_icons()
    log.info("已提取 MC 图标: item=%d block=%d hud=%d（来源 %s）", item, block, hud,
             jar.name if jar else "（无 jar）")
    return {"item": item, "block": block, "hud": hud}
