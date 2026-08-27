"""Mod WebSocket 客户端：连接 / 握手 / 心跳 / 自动重连 / 请求-响应 / 事件。

用法（异步）::

    cfg = load_config()
    client = KeyboardClient(cfg.mod)
    client.on_event(handler)
    await client.start()
    state = await client.request(**build_get_state())
    ...
    await client.close()

请求统一带 `type:"request"`（协议 v2 实现要求），心跳 `{"type":"ping"}`。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection

from ..config import ModConfig
from .errors import ModError, NotConnectedError
from .events import Event, EventBus
from .ratelimit import RateLimiter

logger = logging.getLogger(__name__)

EventHandler = Callable[[Event], Any | Awaitable[Any]]


class KeyboardClient:
    """对 keyboard mod WebSocket 服务的异步客户端。"""

    def __init__(self, config: ModConfig, rate_limiter: RateLimiter | None = None) -> None:
        self._cfg = config
        self._rl = rate_limiter or RateLimiter()
        self._events = EventBus()
        self._ws: ClientConnection | None = None
        self._closed = False
        self._connected = asyncio.Event()
        self._req_lock = asyncio.Lock()
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._run_task: asyncio.Task | None = None
        self._hb_task: asyncio.Task | None = None
        self.protocol: int | None = None
        self._connected_cbs: list[Callable[[], Awaitable[None] | None]] = []

    # ------------------------------------------------------------ 事件订阅
    def on_event(self, callback: EventHandler) -> None:
        self._events.subscribe(callback)

    def remove_event(self, callback: EventHandler) -> None:
        self._events.unsubscribe(callback)

    def on_connected(self, callback: Callable[[], Awaitable[None] | None]) -> None:
        """连接成功回调（含重连成功后触发）。"""
        if callback not in self._connected_cbs:
            self._connected_cbs.append(callback)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # ------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        """启动连接循环（含自动重连），不阻塞。可重复调用（close 后可再次 start）。"""
        self._closed = False
        if self._run_task is None:
            self._run_task = asyncio.create_task(self._run_forever())

    async def wait_connected(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            await self._ws.close()
        self._fail_pending(ModError(None, "连接已关闭"))
        tasks = [t for t in (self._run_task, self._hb_task) if t is not None]
        self._run_task = None
        self._hb_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._connected.clear()

    # ------------------------------------------------------------ 请求
    async def request(self, action: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> Any:
        """发送动作请求并等待对应响应。返回 result；失败抛 ModError。"""
        if not self._connected.is_set() or self._ws is None:
            raise NotConnectedError("尚未连接到 Mod")
        params = params or {}

        from .actions import ACTION_CATEGORY

        await self._rl.acquire(ACTION_CATEGORY.get(action, "other"))
        async with self._req_lock:
            rid = self._next_id
            self._next_id += 1

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = json.dumps({"id": rid, "type": "request", "action": action, "params": params})
        try:
            await self._ws.send(payload)
        except Exception as exc:  # 发送失败
            self._pending.pop(rid, None)
            raise ModError(None, f"发送失败: {exc}") from exc

        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise ModError(None, f"请求超时（action={action}）") from None
        except asyncio.CancelledError:
            self._pending.pop(rid, None)
            raise

    async def get_state(self, timeout: float = 10.0) -> dict[str, Any]:
        """便捷：get_state 快照（协议 §6）。"""
        return await self.request("get_state", {}, timeout=timeout)

    # ------------------------------------------------------------ 内部
    def _url(self) -> str:
        return f"ws://{self._cfg.host}:{self._cfg.port}/?token={self._cfg.token}"

    async def _run_forever(self) -> None:
        while not self._closed:
            try:
                await self._connect_once()
                await self._recv_loop()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - 连接/握手/接收错误
                logger.warning("连接异常: %s", exc)
            finally:
                await self._teardown()
                if not self._closed:
                    await asyncio.sleep(self._cfg.reconnect_s)

    async def _connect_once(self) -> None:
        self._ws = await websockets.connect(
            self._url(),
            open_timeout=10,
            close_timeout=5,
            ping_interval=None,  # 心跳由本层显式发送（协议要求 JSON ping）
        )
        hello = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10))
        if hello.get("type") != "hello" or not hello.get("ok"):
            raise ModError(201, hello.get("error", "握手失败"))
        self.protocol = hello.get("protocol")
        logger.info("已连接 mod（protocol=%s）", self.protocol)
        self._connected.set()
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        for cb in self._connected_cbs:
            result = cb()
            if asyncio.iscoroutine(result):
                await result

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning("收到非法 JSON: %r", raw[:200])
                continue
            await self._handle(msg)

    async def _handle(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "event":
            await self._events.publish(Event(msg.get("event", ""), msg.get("data") or {}))
            return
        rid = msg.get("id")
        if rid is not None:
            fut = self._pending.pop(rid, None)
            if fut is not None and not fut.done():
                if msg.get("ok"):
                    fut.set_result(msg.get("result"))
                else:
                    err = msg.get("error") or {}
                    fut.set_exception(ModError(err.get("code"), err.get("message")))
        # pong 等无需处理

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._closed and self._connected.is_set():
                await asyncio.sleep(self._cfg.heartbeat_s)
                if self._ws is None:
                    break
                await self._ws.send(json.dumps({"type": "ping"}))
        except Exception:  # noqa: BLE001 - 断线时退出心跳
            pass

    async def _teardown(self) -> None:
        if self._hb_task is not None:
            self._hb_task.cancel()
            self._hb_task = None
        self._connected.clear()
        self._fail_pending(ModError(None, "连接断开"))
        self._ws = None

    def _fail_pending(self, error: ModError) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(error)
        self._pending.clear()
