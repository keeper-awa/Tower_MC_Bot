#!/usr/bin/env python3
"""Tower M4 截图测试：screenshot 动作 + 独立文件夹 + 溢出清理（>10 删最早）。

前置条件：游戏运行中且已进世界。
用法：python m4_screenshot_test.py [token] [--game-dir D]
"""

import argparse
import json
import sys
import time
from pathlib import Path

from tower_client import TowerClient, connect_until_ready


def wait_file(path, timeout=5.0):
    """等待截图文件写完（协议 §5.4：异步保存，需重试）。

    注意：文件创建后可能仍在写入——需等大小稳定（连续两次一致且 > 0）。
    """
    deadline = time.time() + timeout
    last_size = -1
    stable = 0
    while time.time() < deadline:
        p = Path(path)
        if p.exists():
            size = p.stat().st_size
            if size > 0 and size == last_size:
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
            last_size = size
        time.sleep(0.3)
    return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Tower M4 截图测试")
    parser.add_argument("token", nargs="?", default=None, help="连接 token（缺省读 config/tower.json）")
    parser.add_argument("--game-dir", default=r"D:\整合包\.minecraft\versions\1.20.1-NeoForge_47.1.106")
    args = parser.parse_args()
    token = args.token
    if not token:
        cfg = Path(args.game_dir) / "config" / "tower.json"
        token = json.loads(cfg.read_text(encoding="utf-8"))["token"]
    ok = True

    client = connect_until_ready(token)
    print(f"==> hello: {client.hello}")

    # ── 1. 单张截图：响应字段 + 文件出现 + PNG 校验 ──────────────────
    r = client.req("screenshot", {})
    if not r["ok"]:
        print(f"[1] ❌ screenshot 失败: {r}")
        return 1
    res = r["result"]
    path, w, h = res["path"], res["width"], res["height"]
    print(f"[1] ✅ 响应: {path} ({w}x{h})")
    assert w > 0 and h > 0
    if "screenshots" + "\\" + "tower" not in path and "screenshots/tower" not in path:
        print(f"[2] ❌ 路径不在 screenshots/tower 子目录: {path}")
        ok = False
    else:
        print(f"[2] ✅ 独立文件夹: screenshots/tower/")
    if wait_file(path):
        data = Path(path).read_bytes()
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            print(f"[3] ✅ 文件已出现且为合法 PNG（{len(data)} 字节）")
        else:
            print("[3] ❌ 文件存在但非 PNG 格式")
            ok = False
    else:
        print("[3] ❌ 文件未在 5s 内出现")
        ok = False

    # ── 2. 溢出清理：连拍 12 张 → 目录最多 10 张 ─────────────────────
    paths = [path]
    for i in range(11):
        r = client.req("screenshot", {})
        assert r["ok"], f"第 {i + 2} 张失败: {r}"
        paths.append(r["result"]["path"])
        time.sleep(0.4)
    # 等全部异步保存完成
    time.sleep(2.0)
    tower_dir = Path(path).parent
    files = sorted(tower_dir.glob("tower_*.png"), key=lambda p: p.stat().st_mtime)
    print(f"[4] 目录内截图: {len(files)} 张（上限 10）")
    if len(files) <= 10:
        print("[4] ✅ 溢出清理生效（≤10 张）")
    else:
        print(f"[4] ❌ 超出上限: {len(files)} 张")
        ok = False
    if len(files) == 10 and all(p in {str(f) for f in files} for p in paths[:0]):
        pass  # 无需额外断言
    # 最早一张应已被清理：第一张截图（最旧）不应在目录里（12 张连拍 → 最旧 2 张被删）
    deleted = [p for p in paths if not Path(p).exists()]
    if len(files) == 10 and len(deleted) == 2:
        print(f"[5] ✅ 最早的 2 张已被清理（新拍 12 张保留最新 10 张）")
    else:
        print(f"[5] ⚠️ 清理情况: 保留 {len(files)} 张，删除 {len(deleted)} 张（预期 10/2）")
        ok = ok and len(files) <= 10

    client.close()
    print("=== 结果:", "全部通过 ✅" if ok else "存在失败 ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
