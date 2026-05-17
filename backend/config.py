"""配置加载模块 — python-dotenv + 系统配置表"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录（backend/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 加载 .env 文件
load_dotenv(PROJECT_ROOT / ".env")


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
    PORT: int = int(os.getenv("FUND_QUANT_PORT", "8000"))
    DEBUG: bool = os.getenv("FUND_QUANT_DEBUG", "false").lower() == "true"

    # CORS
    CORS_ORIGINS: list[str] = os.getenv(
        "FUND_QUANT_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
    ).split(",")

    # AI 默认配置（首次启动写入 system_config 表）
    DEFAULT_AI_ENABLED: bool = True
    DEFAULT_AI_MODEL: str = "deepseek"
    DEFAULT_AI_API_KEY: str = ""
    DEFAULT_AI_BASE_URL: str = "https://api.deepseek.com/v1"

    # 数据源
    TUSHARE_TOKEN: str = os.getenv("TUSHARE_TOKEN", "")

    # 评分阈值
    DEFAULT_BUY_THRESHOLD: float = 3.5
    DEFAULT_SELL_THRESHOLD: float = 2.0


settings = Settings()
