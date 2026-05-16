#!/usr/bin/env bash
# ============================================================================
# 基金量化交易系统 — 一键部署脚本
# 用法: chmod +x deploy.sh && ./deploy.sh [dev]
# ============================================================================

set -euo pipefail

# ── 颜色定义 ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ── 辅助函数 ──────────────────────────────────────────────────────────────
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── 前置检查 ──────────────────────────────────────────────────────────────
info "正在检查部署环境..."

# 检查 Docker
if ! command -v docker &> /dev/null; then
    fail "未检测到 Docker，请先安装: https://docs.docker.com/get-docker/"
fi
ok "Docker 已安装: $(docker --version)"

# 检查 Docker Compose (V2 插件或独立命令)
if docker compose version &> /dev/null; then
    COMPOSE_CMD="docker compose"
    ok "Docker Compose V2 已安装: $(docker compose version)"
elif command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker-compose"
    ok "Docker Compose 已安装: $(docker-compose --version)"
else
    fail "未检测到 Docker Compose，请先安装"
fi

# ── .env 文件检查 ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f .env ]; then
    warn "未找到 .env 文件"
    if [ -f .env.example ]; then
        info "从 .env.example 复制创建 .env ..."
        cp .env.example .env
        warn "请编辑 .env 文件，填写 AI_API_KEY 等必要配置后再重新运行"
        warn "编辑命令: nano .env 或 vim .env"
        exit 0
    else
        fail ".env.example 也不存在，请手动创建 .env 文件"
    fi
fi
ok ".env 文件已就绪"

# ── 创建数据目录 ─────────────────────────────────────────────────────────
if [ ! -d data ]; then
    info "创建数据目录 data/ ..."
    mkdir -p data
fi
ok "数据目录已就绪"

# ── 选择部署模式 ─────────────────────────────────────────────────────────
MODE="${1:-prod}"

if [ "$MODE" = "dev" ]; then
    COMPOSE_FILE="-f docker-compose.dev.yml"
    info "启动开发模式（源码挂载 + 热重载）..."
else
    COMPOSE_FILE="-f docker-compose.yml"
    info "启动生产模式..."
fi

# ── 构建并启动 ────────────────────────────────────────────────────────────
info "正在构建并启动容器..."
$COMPOSE_CMD $COMPOSE_FILE up -d --build

# ── 等待服务就绪 ──────────────────────────────────────────────────────────
info "等待后端服务健康检查通过..."
MAX_WAIT=60
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    echo -n "."
done
echo ""

if [ $WAITED -ge $MAX_WAIT ]; then
    warn "后端服务健康检查超时，请查看日志: $COMPOSE_CMD $COMPOSE_FILE logs backend"
else
    ok "后端服务已就绪"
fi

# ── 部署结果 ──────────────────────────────────────────────────────────────
echo ""
echo "============================================"
ok "基金量化交易系统部署完成！"
echo "============================================"
echo ""

if [ "$MODE" = "dev" ]; then
    echo "  前端 (开发): http://localhost:5173"
    echo "  后端 API:    http://localhost:8000"
    echo "  健康检查:    http://localhost:8000/health"
    echo ""
    echo "  提示: 开发模式下修改代码会自动热重载"
else
    echo "  前端:      http://localhost"
    echo "  后端 API:  http://localhost:8000"
    echo "  健康检查:  http://localhost:8000/health"
    echo "  API 文档:  http://localhost:8000/docs"
fi

echo ""
echo "  查看日志:   $COMPOSE_CMD $COMPOSE_FILE logs -f"
echo "  停止服务:   $COMPOSE_CMD $COMPOSE_FILE down"
echo "  重启服务:   $COMPOSE_CMD $COMPOSE_FILE restart"
echo ""
