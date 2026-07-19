#!/bin/bash
# Docker 全量测试脚本 — 验证并发缓存锁修复效果
# 测试目标：
# 1. 仪表盘数据正常返回（无 TimeoutError）
# 2. 基金详情刷新无超时
# 3. 分析任务超时次数大幅减少（从 229 次降至接近 0）

set -e

echo "=========================================="
echo "Docker 全量测试 — 并发缓存锁修复验证"
echo "=========================================="
echo ""

# 等待服务启动
echo "[1/4] 等待后端服务启动..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/health | grep -q "healthy"; then
        echo "  后端服务已启动 (尝试 $i/30)"
        break
    fi
    sleep 2
done

echo ""
echo "[2/4] 测试仪表盘数据获取（市场概况）..."
START=$(python3 -c "import time; print(int(time.time()*1000))")
SUMMARY=$(curl -s http://localhost:8000/api/analysis/summary 2>/dev/null || echo "FAILED")
END=$(python3 -c "import time; print(int(time.time()*1000))")
DURATION=$(( END - START ))
if echo "$SUMMARY" | grep -q "TimeoutError\|failed\|error"; then
    echo "  ❌ 仪表盘数据包含错误 (耗时 ${DURATION}ms)"
    echo "$SUMMARY" | python3 -m json.tool 2>/dev/null | head -30
else
    echo "  ✅ 仪表盘数据获取成功 (耗时 ${DURATION}ms)"
    # 检查 market_flow 是否有数据
    if echo "$SUMMARY" | grep -q "market_flow"; then
        echo "  ✅ market_flow 字段存在"
    fi
fi

echo ""
echo "[3/4] 测试基金详情刷新..."
# 触发刷新
curl -s -X POST http://localhost:8000/api/funds/refresh-details > /dev/null 2>&1
echo "  刷新任务已触发，等待完成..."

# 轮询刷新状态
REFRESH_START=$(date +%s)
for i in $(seq 1 120); do
    STATUS=$(curl -s http://localhost:8000/api/funds/refresh-details/status 2>/dev/null)
    if echo "$STATUS" | grep -q '"done"\|"failed"'; then
        REFRESH_END=$(date +%s)
        REFRESH_DURATION=$(( REFRESH_END - REFRESH_START ))
        DONE=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('done',0))" 2>/dev/null || echo "?")
        TOTAL=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total',0))" 2>/dev/null || echo "?")
        STAT=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "?")
        echo "  刷新完成: $DONE/$TOTAL 只, 状态=$STAT, 耗时 ${REFRESH_DURATION}s"
        break
    fi
    sleep 3
done

echo ""
echo "[4/4] 测试分析任务..."
ANALYSIS_START=$(date +%s)
echo "  触发分析任务..."
curl -s -X POST http://localhost:8000/api/analysis/trigger > /dev/null 2>&1

# 轮询分析状态（分析可能需要 3-5 分钟）
for i in $(seq 1 120); do
    sleep 5
    ANALYSIS_END=$(date +%s)
    ANALYSIS_DURATION=$(( ANALYSIS_END - ANALYSIS_START ))
    # 检查容器日志是否有分析完成的信息
    if docker logs fund-quant-backend 2>&1 | tail -20 | grep -q "分析完成\|分析流程完成\|因子计算完成"; then
        echo "  分析任务进行中/已完成 (已耗时 ${ANALYSIS_DURATION}s)"
    fi
    # 检查是否有新的分析结果
    if docker logs fund-quant-backend 2>&1 | tail -50 | grep -q "分析流程完成\|所有数据源"; then
        echo "  ✅ 分析任务完成 (耗时 ${ANALYSIS_DURATION}s)"
        break
    fi
    if [ $ANALYSIS_DURATION -gt 600 ]; then
        echo "  ⚠️ 分析任务超过 10 分钟，停止等待"
        break
    fi
done

echo ""
echo "=========================================="
echo "超时统计（关键指标）"
echo "=========================================="
# 统计日志中的超时次数
TIMEOUT_COUNT=$(docker logs fund-quant-backend 2>&1 | grep -c "超时\|TimeoutError" || echo "0")
echo "  总超时次数: $TIMEOUT_COUNT"

# 按接口分类超时
echo ""
echo "按接口分类超时:"
docker logs fund-quant-backend 2>&1 | grep "超时\|TimeoutError" | sed 's/.*\[/[/' | sort | uniq -c | sort -rn | head -20

echo ""
echo "缓存命中统计:"
CACHE_HITS=$(docker logs fund-quant-backend 2>&1 | grep -c "命中缓存\|锁内复用\|首次网络请求\|缓存已填充" || echo "0")
echo "  缓存相关日志: $CACHE_HITS 条"
echo ""
echo "首次网络请求（应每个共享数据只出现 1 次）:"
docker logs fund-quant-backend 2>&1 | grep "首次网络请求\|缓存已填充" | head -20

echo ""
echo "锁内复用（应出现多次，证明锁生效）:"
docker logs fund-quant-backend 2>&1 | grep "锁内复用" | head -20

echo ""
echo "=========================================="
echo "测试完成"
echo "=========================================="
