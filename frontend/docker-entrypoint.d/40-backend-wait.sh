#!/bin/sh
# 等待 backend 就绪（TCP 连通性检查），避免 nginx 启动瞬间 API 出现瞬态 502。
#
# 背景（2026-08-23 修复）：
# 前后端同处自定义桥接网络 fund-quant-network，Docker 内置 DNS 可解析
# 服务名 backend。nginx.conf 使用 set 变量 + resolver 127.0.0.11，在运行时
# 动态解析 backend 并直连容器，启动时不依赖对端可达（不会 [emerg] 崩溃）。
# 因此本脚本只需做"就绪等待"的体验优化，**绝不改写 nginx 配置、绝不回退到
# 宿主机网关 IP**（容器间经网关不可达，会 Connection refused → 502）。
#
# 即使 backend 暂时不可达，本脚本超时后也照常启动 nginx：静态页面可用，
# API 在 backend 就绪后由 nginx 运行时 resolver 自愈。

BACKEND_HOST="${BACKEND_HOST:-backend}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
MAX_RETRIES="${BACKEND_WAIT_RETRIES:-30}"
SLEEP_SECS="${BACKEND_WAIT_INTERVAL:-2}"

echo "→ Waiting for backend (${BACKEND_HOST}:${BACKEND_PORT}) to be ready..."

i=1
while [ "${i}" -le "${MAX_RETRIES}" ]; do
    # busybox wget 探测 /health；backend 在共享网络上可达即可解析主机名
    if wget -q -T 2 -O /dev/null "http://${BACKEND_HOST}:${BACKEND_PORT}/health" 2>/dev/null; then
        echo "✓ backend ready at ${BACKEND_HOST}:${BACKEND_PORT}"
        exit 0
    fi
    echo "  attempt ${i}/${MAX_RETRIES}: backend not ready, waiting ${SLEEP_SECS}s..."
    sleep "${SLEEP_SECS}"
    i=$((i + 1))
done

echo "⚠ WARNING: backend not ready after ${MAX_RETRIES} attempts; starting nginx anyway (static pages OK, API self-heals once backend is up)"
exit 0
