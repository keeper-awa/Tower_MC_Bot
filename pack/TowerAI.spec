# -*- mode: python ; coding: utf-8 -*-
"""Tower AI 大脑 exe 打包（PyInstaller onedir）。

入口 pack/launcher.py（等价 keeper.cli run-daemon --gui，pywebview 内嵌 webui）。
产物：dist/TowerAI/（TowerAI.exe + _internal/，含 keeper/brain/webui/skills）。
onedir（非 onefile）：config.yaml 可写（game_dir 设置/日志持久化）。
用法：pyinstaller pack/TowerAI.spec --clean --noconfirm
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# pack/ 的上一级 = 仓库根（SPECPATH 是 PyInstaller 注入的 spec 所在目录）
ROOT = Path(SPECPATH).parent

datas = [
    # webui 前端产物（frozen 后 app.py WEBUI_DIST → _MEIPASS/keeper/webui/dist）
    (str(ROOT / "keeper" / "webui" / "dist"), "keeper/webui/dist"),
    # 大脑默认配置模板 → _MEIPASS/brain/config.yaml（注意：datas 目标是「目录」）
    (str(ROOT / "brain" / "config.yaml"), "brain"),
    # 技能（打进 exe 作兜底；exe 旁 skills/ 优先，可扩展无需重打包）
    (str(ROOT / "brain" / "skills"), "brain/skills"),
    # 默认壁纸（frozen 后 app.py WALLPAPER_DIR → _MEIPASS/wallpaper）
    (str(ROOT / "wallpaper"), "wallpaper"),
]

# pywebview 运行时动态 import 平台后端（winforms 走 pythonnet/clr）——
# 静态分析抓不到，必须 collect_all 收全 platforms 数据/二进制。
wv_datas, wv_binaries, wv_hidden = collect_all("webview")
datas += wv_datas
binaries = wv_binaries
hiddenimports = (
    wv_hidden
    + collect_submodules("keeper")
    + collect_submodules("brain")
    + collect_submodules("skills")
    # pythonnet（winforms 后端）与平台模块显式收
    + ["clr", "pythonnet", "webview.platforms.winforms", "webview.platforms.edgechromium"]
    + collect_submodules("clr")
    # httpx（keeper/daemon/manager.py 引入；httpx 子模块多为动态 import，需显式收全）
    + collect_submodules("httpx")
)

a = Analysis(
    [str(ROOT / "pack" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TowerAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI 模式（--noconsole）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TowerAI",
)
