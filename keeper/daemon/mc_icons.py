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


# ── HUD 图标（生命/饥饿）：从 gui/icons.png 裁剪满/半/空 → mc-icons/ ──
# 1.20.1 icons.png 第一行 (y=0) 布局：红心 x=54/63/72，鸡腿 x=108/117/126
_HUD_CROPS = {
    "heart.png": (54, 0, 63, 9),
    "heart_half.png": (63, 0, 72, 9),
    "heart_empty.png": (72, 0, 81, 9),
    "food.png": (108, 0, 117, 9),
    "food_half.png": (117, 0, 126, 9),
    "food_empty.png": (126, 0, 135, 9),
}


def _gen_hud_icons(jar: Path) -> int:
    """从 gui/icons.png 裁剪心形/鸡腿（满/半/空）图标，返回生成数量。"""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        log.warning("Pillow 不可用，跳过 HUD 图标")
        return 0
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with zipfile.ZipFile(jar) as z:
            data = z.read("assets/minecraft/textures/gui/icons.png")
    except Exception as exc:  # noqa: BLE001
        log.warning("读取 gui/icons.png 失败: %s", exc)
        return 0
    import io

    try:
        sheet = Image.open(io.BytesIO(data)).convert("RGBA")
        for name, (l, t, r, b) in _HUD_CROPS.items():
            target = ICONS_DIR / name
            if target.exists():
                continue
            sheet.crop((l, t, r, b)).resize((16, 16), Image.NEAREST).save(target)
            count += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("生成 HUD 图标失败: %s", exc)
        return 0
    if count:
        log.info("已生成 HUD 图标: %d 个", count)
    return count


def ensure_icons(game_dir: str | Path) -> dict[str, int]:
    """提取物品/方块图标 + HUD 图标到 mc-icons/。返回各分类新增数量。

    幂等：已提取过则 count=0。找不到 jar 返回 {"item": 0, "block": 0, "hud": 0}。
    """
    jar = _find_jar(game_dir)
    if jar is None:
        log.warning("未找到 Minecraft 版本 jar（%s），物品图标不可用", game_dir)
        return {"item": 0, "block": 0, "hud": 0}
    item = _extract_textures(jar, "assets/minecraft/textures/item/", ITEM_DIR)
    block = _extract_textures(jar, "assets/minecraft/textures/block/", BLOCK_DIR)
    # HUD 图标（心形/鸡腿）由用户手动裁剪提供，不再自动生成，避免错误坐标覆盖
    log.info("已提取 MC 图标: item=%d block=%d（来源 %s）", item, block, jar.name)
    return {"item": item, "block": block, "hud": 0}
