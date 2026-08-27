"""pywebview（WebView2）桌面壳：打开本地面板窗口。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_gui(url: str, title: str = "KeeperMC", width: int = 1280, height: int = 800) -> None:
    """在 WebView2 窗口中打开本地管理面板（阻塞直到窗口关闭）。"""
    try:
        import webview
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f"未安装 pywebview，请先安装（pip install pywebview），或改用浏览器访问 {url}") from exc

    logger.info("打开桌面壳: %s", url)
    webview.create_window(title, url, width=width, height=height, min_size=(960, 640))
    webview.start()
