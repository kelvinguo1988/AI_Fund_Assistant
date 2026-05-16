# ============================================================================
# 基金量化交易系统 — 后端 Dockerfile
# 基于 python:3.9-slim，适配 SQLAlchemy 2.0 + aiosqlite
# ============================================================================

FROM python:3.9-slim AS base

# 元数据
LABEL maintainer="fund-quant-team"
LABEL description="基金量化交易系统后端服务"

# 设置环境变量：Python 非交互式 + 不生成 .pyc + 实时日志
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统编译依赖（akshare/numpy 可能需要 C 扩展）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libffi-dev \
        && rm -rf /var/lib/apt/lists/*

# 设置工作目录为项目根目录（与 config.py 中 PROJECT_ROOT 对齐）
WORKDIR /app

# 先复制 requirements.txt 利用 Docker 缓存层
COPY requirements.txt /app/backend/requirements.txt

# 安装 Python 依赖
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 复制后端代码
COPY . /app/backend/

# 创建数据目录（SQLite 数据库将存放于此）
RUN mkdir -p /app/data

# 暴露 FastAPI 服务端口
EXPOSE 8000

# 健康检查：等待 5 秒后开始，每 30 秒检查一次
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 启动命令：从项目根目录 /app 执行 uvicorn
# 这样 Python 能正确解析 backend.main 模块路径
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
