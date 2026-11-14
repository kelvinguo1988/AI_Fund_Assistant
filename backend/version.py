"""系统版本 — 单一事实源（后端 FastAPI / 前端顶栏 / Docker 构建共用）

版本语义（手动维护，随功能里程碑递进）:
- 2.0.0：11 因子体系 + 质量过滤 + 实时估值（三源降级）+ 自动回测 +
  投资复盘/基金 PK + 组合 X 光 + AI Agent（15 工具/技能工具化/多 provider）

BUILD_SHA 来源优先级: 环境变量 APP_BUILD_SHA（Docker build 注入）>
.app_git_sha 文件 > None。本地开发无构建注入时显示纯版本号。
"""

import os
from pathlib import Path

APP_VERSION = "2.0.0"

_ENV_SHA = os.getenv("APP_BUILD_SHA", "").strip()
_SHA_FILE = Path(__file__).resolve().parent / ".git_sha"

if _ENV_SHA:
    BUILD_SHA = _ENV_SHA[:7]
elif _SHA_FILE.exists():
    BUILD_SHA = _SHA_FILE.read_text().strip()[:7] or None
else:
    BUILD_SHA = None


def version_display() -> str:
    """顶栏展示串：v2.0.0 或 v2.0.0 (a1b2c3d)"""
    return f"v{APP_VERSION}" + (f" ({BUILD_SHA})" if BUILD_SHA else "")
