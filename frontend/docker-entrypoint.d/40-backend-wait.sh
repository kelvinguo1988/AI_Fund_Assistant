#!/bin/sh
# 等待 backend 主机名可解析
# QNAP Container Station 兼容：links 写入 /etc/hosts 可能需要时间，
# 而 nginx.conf 已使用 set 变量延迟解析，本脚本作为额外保护层

BACKEND_HOST="${BACKEND_HOST:-backend}"
MAX_RETRIES="${BACKEND_WAIT_RETRIES:-30}"
SLEEP_SECS="${BACKEND_WAIT_INTERVAL:-2}"

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

echo "⚠ WARNING: ${BACKEND_HOST} not resolvable after ${MAX_RETRIES} attempts, starting nginx anyway"
exit 0
