#!/usr/bin/env python3
"""Tower mod 示例客户端（协议 v1，WebSocket）。

两种用法：
1. 命令行演示（已进游戏世界）：python tower_client.py [--token T] [--port P]
2. 作为库导入（AI 进程集成）：

   from tower_client import TowerClient

   client = TowerClient(token)              # 连接（token 从 config/tower.json 读）
   client.ping()                            # 心跳
   events = client.drain_events()           # 取出在途事件（M1.3 起有中继事件）

依赖：Python 3.10+，websockets 库（pip install websockets）。
"""

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from websockets.sync.client import connect


class TowerClient:
    """同步 WebSocket 客户端（《Tower协议.md》v1）。"""

    def __init__(self, token, host="127.0.0.1", port=24778):
        self._recv_lock = threading.Lock()  # 同步 websocket 不支持并发 recv（Brain 线程 + API 线程）
        self.ws = connect(f"ws://{host}:{port}/?token={token}")
        self._id = 0
        self._events = []
        # 握手：hello 消息（鉴权失败会随后断开）
        while True:
            msg = json.loads(self.ws.recv(timeout=10))
            if msg.get("type") == "event":
                self._events.append(msg)
            elif msg.get("type") == "hello":
                self.hello = msg
                break
        if not self.hello.get("ok"):
            self.ws.close()
            raise RuntimeError(f"鉴权失败: {self.hello}")

    def send(self, payload: dict):
        self.ws.send(json.dumps(payload))

    def recv(self, timeout=10):
        """接收一条非事件消息；期间到达的事件缓存到队列。

        注意：总超时 = timeout 秒（迭代式实现，避免事件流不断时
        递归 recv 每次重置超时导致永不返回）。

        容错：技能执行线程与心跳线程偶发并发 recv，websockets 抛
        "cannot call recv"——捕获后短暂等待重试（另一个 recv 瞬时结束）。
        """
        with self._recv_lock:
            deadline = time.time() + timeout
            retries = 0
            while True:
                remaining = max(0.1, deadline - time.time())
                try:
                    msg = json.loads(self.ws.recv(timeout=remaining))
                    retries = 0
                except Exception as e:
                    if "cannot call recv" in str(e) and retries < 60:
                        # 另一线程的 recv 正在占用 socket：让出一点时间再重试（限 60 次≈3s 防死循环）
                        retries += 1
                        time.sleep(0.05)
                        continue
                    raise
                if msg.get("type") == "event":
                    self._events.append(msg)
                    continue
                return msg

    def req(self, action, params=None):
        """发送动作请求并等待匹配 id 的响应；在途事件缓存到队列。

        recv 并发冲突（daemon API 线程与技能线程共享 client）时整体重发请求
        （最多 3 次），避免动作失败中断技能。
        """
        self._id += 1
        req_id = self._id
        for attempt in range(3):
            self.send({"type": "request", "id": req_id, "action": action, "params": params or {}})
            try:
                while True:
                    msg = self.recv()
                    if msg.get("id") == req_id:
                        return msg
                    # 其他消息（如 pong）忽略
            except Exception as e:
                if "cannot call recv" in str(e) and attempt < 2:
                    time.sleep(0.1)
                    continue  # 重发请求（同一 req_id，响应仍匹配）
                raise
        raise RuntimeError(f"{action} 请求因 recv 并发冲突重试失败")

    def ok(self, action, params=None):
        """req 的便捷版：失败时抛异常，成功返回 result。"""
        resp = self.req(action, params)
        if not resp.get("ok"):
            raise RuntimeError(f"{action} 失败: {resp.get('error')}")
        return resp.get("result")

    def ping(self):
        self.send({"type": "ping"})
        return self.recv()

    def drain_events(self):
        """取出缓存的全部事件并清空。"""
        events, self._events = self._events, []
        return events

    def close(self):
        self.ws.close()


def connect_until_ready(token, port=24778, tries=30):
    """连接 Tower 并等待前置链路就绪（prereq=connected），最多 tries 次重试。

    大脑断开会触发协议 §2.4 断线清理（归零+断开前置链路），重连后链路自动恢复——
    所有验收脚本用本函数等待就绪后再开始。
    """
    import time as _time
    for i in range(tries):
        try:
            client = TowerClient(token, port=port)
            if client.hello.get("prereq") == "connected":
                return client
            print(f"==> 前置链路未就绪（{client.hello.get('prereq')}），1s 后重连（{i + 1}/{tries}）")
            client.close()
        except Exception as e:
            print(f"==> 连接失败: {e}，1s 后重试（{i + 1}/{tries}）")
        _time.sleep(1)
    raise SystemExit("前置链路等待超时（游戏是否已启动并进世界？）")


def _default_game_dir() -> Path:
    """从 brain/config.yaml 读取游戏目录（绝对路径）。"""
    cfg = Path(__file__).resolve().parent / "config.yaml"
    try:
        import yaml

        return Path(yaml.safe_load(cfg.read_text(encoding="utf-8"))["connection"]["game_dir"])
    except Exception:
        return Path(__file__).resolve().parent.parent


def demo() -> int:
    """M1.2 演示：连接 / hello（含 prereq）/ 心跳 / 断开。"""
    parser = argparse.ArgumentParser(description="Tower 示例客户端（M1.2 演示）")
    parser.add_argument("--token", default=None, help="连接 token（缺省读游戏 config/tower.json）")
    parser.add_argument("--port", type=int, default=24778)
    parser.add_argument("--game-dir", default=None, help="游戏目录（缺省读 brain/config.yaml 绝对路径）")
    args = parser.parse_args()
    token = args.token
    if not token:
        game_dir = Path(args.game_dir) if args.game_dir else _default_game_dir()
        cfg = game_dir / "config" / "tower.json"
        if cfg.exists():
            token = json.loads(cfg.read_text(encoding="utf-8"))["token"]
        else:
            raise SystemExit(f"未找到 {cfg}，请用 --token 指定")

    client = TowerClient(token, port=args.port)
    h = client.hello
    print(f"==> 已连接 protocol={h.get('protocol')} mod={h.get('mod')} version={h.get('version')} "
          f"prereq={h.get('prereq')}")

    pong = client.ping()
    print(f"==> ping -> {pong}")

    time.sleep(0.3)
    client.close()
    print("==> 演示完成，连接已关闭")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(demo())
