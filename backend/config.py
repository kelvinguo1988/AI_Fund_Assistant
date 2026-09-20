"""配置加载模块 — python-dotenv + 系统配置表"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录（backend/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 加载 .env 文件
load_dotenv(PROJECT_ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    """导入期防御：坏整型环境变量（如 PORT=abc）回退默认值 + 告警，
    不能让模块导入直接崩掉整个进程（3.9 类体内不可调 staticmethod，放模块级）
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        import logging

        logging.getLogger(__name__).warning(
            f"环境变量 {name}={raw!r} 非法，回退默认值 {default}"
        )
        return default


class Settings:
    """全局配置，优先从 .env 读取，运行时配置从 system_config 表读取"""

    # 数据库
    DATABASE_DIR: str = os.getenv("FUND_QUANT_DATABASE_DIR", str(PROJECT_ROOT / "data"))
    DATABASE_NAME: str = os.getenv("FUND_QUANT_DATABASE_NAME", "fund_quant.db")

    @property
    def DATABASE_URL(self) -> str:
        """异步 SQLAlchemy 连接串"""
        db_path = Path(self.DATABASE_DIR) / self.DATABASE_NAME
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path}"

    # 服务
    HOST: str = os.getenv("FUND_QUANT_HOST", "0.0.0.0")
    PORT: int = _env_int("FUND_QUANT_PORT", 8000)
    DEBUG: bool = os.getenv("FUND_QUANT_DEBUG", "false").lower() == "true"

    # CORS
    CORS_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv(
            "FUND_QUANT_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
        ).split(",")
        if o.strip()
    ]

    # AI 默认配置已全部走 system_config 表（2026-09-12 收官：删除
    # 从未被读取的 DEFAULT_AI_* 三键，AI Key/模型在 Web 界面配置）

    # 数据源
    JOINQUANT_USER: str = os.getenv("JOINQUANT_USER", "")
    JOINQUANT_PASSWORD: str = os.getenv("JOINQUANT_PASSWORD", "")

    # 评分阈值走 system_config 表（原 DEFAULT_BUY/SELL_THRESHOLD 常量从未被读取，已删）


settings = Settings()
