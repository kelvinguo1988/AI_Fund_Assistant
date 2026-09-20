# syntax=docker/dockerfile:1
# ============================================================================
# 基金量化交易系统 — 后端 Dockerfile
# 基于 python:3.9-slim，适配 SQLAlchemy 2.0 + aiosqlite
#
# 镜像源策略（ARG 控制，默认官方源）：
#   - GitHub Actions runner 在海外，走 deb.debian.org / pypi.org 才有速度
#   - 国内设备（QNAP）本地构建时在 docker-compose.yml 传参切阿里云：
#       APT_MIRROR_HOST=mirrors.aliyun.com
#       PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
# ============================================================================

FROM python:3.9-slim AS base

ARG APT_MIRROR_HOST=""
ARG PIP_INDEX_URL=https://pypi.org/simple

LABEL maintainer="fund-quant-team"
LABEL description="基金量化交易系统后端服务"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

# 显式传入 APT_MIRROR_HOST 时才替换 apt 源（deb.debian.org 与镜像站路径结构一致）
# gcc/g++/libffi-dev：py-mini-racer 等无 aarch64 预编译 wheel 的依赖在构建期需要编译链
RUN if [ -n "$APT_MIRROR_HOST" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR_HOST|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null \
      || sed -i "s|deb.debian.org|$APT_MIRROR_HOST|g" /etc/apt/sources.list 2>/dev/null \
      || true; fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
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
