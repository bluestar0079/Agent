"""配置与路径。刻意不引入 python-dotenv：只用十几行解析 .env，少一个依赖。"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def load_env(path: Path | None = None) -> None:
    """把 .env 读进 os.environ（已存在的环境变量优先，不覆盖）。"""
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Settings:
    def __init__(self) -> None:
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.model = os.getenv("QWEN_MODEL", "qwen3.8-flash")
        self.max_steps = int(os.getenv("MAX_STEPS", "14"))
        self.max_tool_calls = int(os.getenv("MAX_TOOL_CALLS", "12"))

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "未配置 DASHSCOPE_API_KEY。请复制 .env.example 为 .env 并填写，"
                "或改用 --provider mock 跑离线流程。"
            )
        return self.api_key
