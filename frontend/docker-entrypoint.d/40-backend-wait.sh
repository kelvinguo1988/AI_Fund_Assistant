#!/bin/sh
# 等待 backend 主机名可解析；全部失败时将 nginx 配置中的 backend
# 替换为默认网关 IP（宿主机），实现 host-gateway 的手动兜底。
#
# 兜底背景（2026-08-23）：
# QNAP Container Station 部分版本的 Docker 不支持 extra_hosts 的
# host-gateway 特殊值（需 Docker >= 20.10），links 也可能被忽略 →
# /etc/hosts 无 backend 条目且 Docker DNS 解析不到 →
# nginx [emerg] host not found in upstream 崩溃循环。
# backend 已通过 ports 8000:8000 映射到宿主机，
# 默认网关 IP + 8000 必然可达，故直接改写 nginx 配置直连。
#
# nginx.conf 已使用 set 变量延迟解析：本脚本的等待与兜底即使全部
# 失败，nginx 也能正常启动（静态页面可用，API 走 resolver 动态解析）。

BACKEND_HOST="${BACKEND_HOST:-backend}"
MAX_RETRIES="${BACKEND_WAIT_RETRIES:-30}"
SLEEP_SECS="${BACKEND_WAIT_INTERVAL:-2}"
NGINX_CONF="/etc/nginx/conf.d/default.conf"

echo "→ Waiting for ${BACKEND_HOST} to be resolvable..."

i=1
while [ "${i}" -le "${MAX_RETRIES}" ]; do
    if getent hosts "${BACKEND_HOST}" > /dev/null 2>&1; then
        echo "✓ ${BACKEND_HOST} resolved successfully"
        exit 0
    fi
    echo "  attempt ${i}/${MAX_RETRIES}: ${BACKEND_HOST} not yet resolvable, waiting ${SLEEP_SECS}s..."
    sleep "${SLEEP_SECS}"
    i=$((i + 1))
done

# ── 兜底：backend → 默认网关（宿主机）───────────────────────────────
GATEWAY=$(ip route 2>/dev/null | awk '/default/ {print $3; exit}')
if [ -n "${GATEWAY}" ]; then
    # 写 /etc/hosts（供 getent/诊断工具使用）
    echo "${GATEWAY} ${BACKEND_HOST}" >> /etc/hosts
    # 改写 nginx 配置为网关 IP 直连（IP 不走 resolver，绕开 DNS 依赖）
    if [ -f "${NGINX_CONF}" ] && sed -i "s|http://${BACKEND_HOST}:8000|http://${GATEWAY}:8000|g" "${NGINX_CONF}"; then
        echo "✓ fallback: pinned ${BACKEND_HOST} → default gateway ${GATEWAY} (${NGINX_CONF} + /etc/hosts)"
        exit 0
    fi
    echo "✓ fallback: pinned ${BACKEND_HOST} → ${GATEWAY} in /etc/hosts (nginx conf not rewritten)"
    exit 0
fi

echo "⚠ WARNING: ${BACKEND_HOST} not resolvable after ${MAX_RETRIES} attempts and no gateway fallback available; starting nginx anyway (static pages OK, API may 502)"
exit 0
