#!/usr/bin/env python3
"""从 minecraft-data（1.20）生成 kc.RECIPES 原始数据（物品 id → 具体材料 id + 数量）。

用法：python tools/gen_recipes.py --items tools/items_1.20.json --recipes tools/recipes_1.20.json
输出：可粘贴到 kc.py 的 RECIPES 字典（材料保留具体 minecraft:xxx id，由 craft_chain 递归解析）。

设计：
- 聚合同一结果的多变体，优先「材料数量最少」的配方（如 stick 用任意木板）
- 保留具体材料 id（minecraft:oak_log），craft_chain 运行时做完整递归追溯
- 排除：不可获取物品、材料含自身的自引用、无材料配方
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain" / "skills"))
from kc import UNOBTAINABLE, is_unobtainable  # noqa: E402


def id_to_name(items, iid) -> str:
    it = items[iid]
    return f"minecraft:{it['name']}"


def count_materials(shape) -> dict:
    """统计 shaped 配方的材料 id → 数量。"""
    counts = {}
    for row in shape:
        for cell in row:
            if cell is None:
                continue
            if isinstance(cell, list):  # 可选材料（取第一个即可）
                counts[cell[0]] = counts.get(cell[0], 0) + 1
                break
            counts[cell] = counts.get(cell, 0) + 1
    return counts


def _is_decomposition(result_name: str, materials: dict) -> bool:
    """判断是否为分解型配方：产物是基础材料（coal/iron_ingot/diamond 等），
    材料含其方块版（coal_block/iron_block/diamond_block）。"""
    # 常见 材料↔方块 对
    pairs = {
        "coal": "coal_block", "iron_ingot": "iron_block", "gold_ingot": "gold_block",
        "diamond": "diamond_block", "emerald": "emerald_block", "redstone": "redstone_block",
        "lapis_lazuli": "lapis_block", "quartz": "quartz_block", "netherite_ingot": "netherite_block",
        "copper_ingot": "copper_block", "raw_iron": "raw_iron_block", "raw_gold": "raw_gold_block",
        "raw_copper": "raw_copper_block", "brick": "bricks", "clay_ball": "clay",
        "glowstone_dust": "glowstone", "bone": "bone_block", "amethyst_shard": "amethyst_block",
    }
    name = result_name[len("minecraft:"):] if result_name.startswith("minecraft:") else result_name
    for mat in materials:
        mname = mat[len("minecraft:"):] if mat.startswith("minecraft:") else mat
        if pairs.get(name) == mname:
            return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", default="tools/items_1.20.json")
    parser.add_argument("--recipes", default="tools/recipes_1.20.json")
    args = parser.parse_args()

    items_list = json.load(open(args.items, encoding="utf-8"))
    items = {it["id"]: it for it in items_list}
    recipes = json.load(open(args.recipes, encoding="utf-8"))

    out = {}
    skipped = {"unobtainable": 0, "circular": 0, "empty": 0, "unparsed": 0, "decomposition": 0}
    for result_id_str, variants in recipes.items():
        result_id = int(result_id_str)
        result_name = id_to_name(items, result_id)
        if is_unobtainable(result_name) or result_name in UNOBTAINABLE:
            skipped["unobtainable"] += 1
            continue

        best = None
        for v in variants:
            if "inShape" in v:
                mat_counts = count_materials(v["inShape"])
                shape = v["inShape"]
                rows = len(shape)
                cols = max((len(r) for r in shape), default=0)
                # 2x2 内能放下的形状 → 背包个人合成格（grid=2x2）；否则需工作台
                grid = "2x2" if rows <= 2 and cols <= 2 else "3x3"
            elif "ingredients" in v:
                mat_counts = {}
                for ing in v["ingredients"]:
                    if isinstance(ing, list):
                        mat_counts[ing[0]] = mat_counts.get(ing[0], 0) + 1
                        break
                    mat_counts[ing] = mat_counts.get(ing, 0) + 1
                grid = "2x2" if len(mat_counts) <= 4 else "3x3"
            else:
                skipped["unparsed"] += 1
                continue
            if not mat_counts:
                skipped["empty"] += 1
                continue
            mats = {id_to_name(items, mid): cnt for mid, cnt in mat_counts.items()}
            yield_n = v.get("result", {}).get("count", 1)
            score = len(mats)
            if best is None or score < best[0]:
                best = (score, {"materials": mats, "grid": grid, "yield": yield_n})

        if best is None:
            continue
        _, recipe = best
        if result_name in recipe["materials"]:
            skipped["circular"] += 1
            continue
        # 排除「分解型配方」：产物是基础材料，材料含其方块版（coal←coal_block、iron_ingot←iron_block）
        # 这类配方会造成 材料↔方块 无限循环，且分解不划算（应直接挖矿得材料）
        if _is_decomposition(result_name, recipe["materials"]):
            skipped["decomposition"] += 1
            continue
        out[result_name] = recipe

    print(f"# 生成 {len(out)} 个配方（跳过：不可获取={skipped['unobtainable']} "
          f"自引用={skipped['circular']} 分解型={skipped['decomposition']} "
          f"空={skipped['empty']} 未解析={skipped['unparsed']}）\n")
    print("RECIPES = {")
    for item_id in sorted(out):
        r = out[item_id]
        mats = ", ".join(f'"{k}": {v}' for k, v in r["materials"].items())
        print(f'    "{item_id}": {{"materials": {{{mats}}}, "grid": "{r["grid"]}", '
              f'"yield": {r["yield"]}}},')
    print("}")


if __name__ == "__main__":
    main()
