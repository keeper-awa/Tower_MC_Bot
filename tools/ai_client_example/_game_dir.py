"""统一游戏目录解析：从项目 brain/config.yaml 读取（绝对路径，唯一配置源）。

各测试/示例脚本不再各自硬编码某台机器的路径，统一走这里；
换机器/换游戏路径只需改 brain/config.yaml 一处。
"""

from pathlib import Path


def default_game_dir() -> Path:
    """返回配置中的游戏目录（绝对路径）。配置缺失/异常时回退到项目根。"""
    root = Path(__file__).resolve().parent.parent.parent  # tools/ai_client_example -> 项目根
    cfg_path = root / "brain" / "config.yaml"
    try:
        import yaml

        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        return Path(data["connection"]["game_dir"])
    except Exception:
        return root
