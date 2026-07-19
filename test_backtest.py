#!/usr/bin/env python3
"""本地回测脚本 — 通过项目原有 AKShare 接口获取广发远见智选混合C(016874) 数据并计算 7 因子

用法:
    cd AI_Fund_Assistant
    python test_backtest.py

无需额外安装依赖，使用项目已有的 akshare + numpy。
"""

import asyncio
import json
import logging
import sys
import time

# 确保项目根目录在 sys.path 中
sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_backtest")

# ═══════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════

FUND_CODE = "016874"       # 广发远见智选混合C（场外 OTC 基金）
FUND_NAME = "广发远见智选混合C"
PERIOD = 365               # 回看天数（覆盖最大因子窗口 252 天）

# 7 因子配置（与 database.py 初始化种子数据一致）
FACTORS = [
    {
        "code": "short_momentum",
        "name": "短期动量",
        "params": json.dumps({"window": 20}),
        "direction": "positive",
        "weight": 1.2,
        "normalization": "cross_sectional_zscore",
    },
    {
        "code": "mid_momentum",
        "name": "中期动量",
        "params": json.dumps({"window": 60}),
        "direction": "positive",
        "weight": 1.2,
        "normalization": "cross_sectional_zscore",
    },
    {
        "code": "inv_volatility",
        "name": "波动率倒数",
        "params": json.dumps({"window": 60}),
        "direction": "positive",
        "weight": 1.0,
        "normalization": "cross_sectional_zscore",
    },
    {
        "code": "drawdown_recovery",
        "name": "回撤修复度",
        "params": json.dumps({"window": 252}),
        "direction": "positive",
        "weight": 0.8,
        "normalization": "none",
    },
    {
        "code": "return_risk_ratio",
        "name": "收益风险比",
        "params": json.dumps({"window": 60, "epsilon": 0.0001}),
        "direction": "positive",
        "weight": 0.8,
        "normalization": "cross_sectional_zscore",
    },
    {
        "code": "momentum_accel",
        "name": "动量加速度",
        "params": json.dumps({"short_window": 20, "mid_window": 60}),
        "direction": "positive",
        "weight": 0.5,
        "normalization": "cross_sectional_zscore",
    },
    {
        "code": "trend_consistency",
        "name": "趋势一致性",
        "params": json.dumps({"short_window": 20, "mid_window": 60}),
        "direction": "positive",
        "weight": 0.5,
        "normalization": "cross_sectional_zscore",
    },
]


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════

async def main():
    sep = "═" * 60

    print(f"\n{sep}")
    print(f"  本地回测测试 — 原有 AKShare 接口 + 7 因子")
    print(f"{sep}")
    print(f"  目标基金: {FUND_NAME} ({FUND_CODE})")
    print(f"  回看天数: {PERIOD} 天")
    print(f"  数据源:   AKShare (天天基金)")

    # ── 1. 通过 AKShareAdapter 获取基金数据 ──
    print(f"\n{sep}")
    print(f"  Step 1: 获取基金净值数据")
    print(f"{sep}")

    from backend.data_sources.akshare_adapter import AKShareAdapter

    adapter = AKShareAdapter()
    t0 = time.time()
    fund_data = await adapter.get_fund_data(FUND_CODE, period=PERIOD, fund_type="otc")
    elapsed = time.time() - t0

    if not fund_data.close_history:
        print(f"  ❌ 获取失败！close_history 为空。耗时 {elapsed:.1f}s")
        sys.exit(1)

    n = len(fund_data.close_history)
    print(f"  ✅ 数据获取成功！耗时 {elapsed:.1f}s")
    print(f"     基金名称:   {fund_data.name or FUND_NAME}")
    print(f"     基金代码:   {fund_data.code}")
    print(f"     净值数据:   {n} 条")
    print(f"     最新净值:   {fund_data.close}")
    print(f"     数据日期:   {fund_data.date}")
    if fund_data.date_history:
        print(f"     数据区间:   {fund_data.date_history[0][:10]} ~ {fund_data.date_history[-1][:10]}")
    if fund_data.bond_yield is not None:
        print(f"     国债收益率: {fund_data.bond_yield}%")
    if fund_data.benchmark_history:
        print(f"     基准数据:   {len(fund_data.benchmark_history)} 条 (沪深300)")

    # 净值走势概览
    closes = fund_data.close_history
    recent_20 = closes[-20:] if len(closes) >= 20 else closes
    recent_60 = closes[-60:] if len(closes) >= 60 else closes
    import numpy as np
    print(f"\n  📈 近期走势:")
    print(f"     20日涨跌: {((closes[-1] / closes[-20]) - 1) * 100:+.2f}%" if len(closes) >= 20 else "")
    print(f"     60日涨跌: {((closes[-1] / closes[-60]) - 1) * 100:+.2f}%" if len(closes) >= 60 else "")
    if len(closes) >= 252:
        print(f"    252日涨跌: {((closes[-1] / closes[-252]) - 1) * 100:+.2f}%")
    peak = max(closes)
    dd = (1 - closes[-1] / peak) * 100
    print(f"     历史最高:   {peak:.4f}  当前回撤: {dd:.2f}%")

    # ── 2. 计算 7 因子 ──
    print(f"\n{sep}")
    print(f"  Step 2: 计算 7 因子")
    print(f"{sep}")

    from backend.engines.factor_engine import factor_engine

    t0 = time.time()
    results = factor_engine.calculate_all(fund_data, FACTORS)
    elapsed = time.time() - t0

    print(f"  计算耗时: {elapsed * 1000:.1f}ms\n")

    print(f"  {'因子名称':<12} {'原始值':>10} {'评分':>8} {'方向':>6} {'权重':>6}")
    print(f"  {'─' * 12} {'─' * 10} {'─' * 8} {'─' * 6} {'─' * 6}")

    total_weight = 0.0
    weighted_score = 0.0

    for r, f in zip(results, FACTORS):
        w = f.get("weight", 1.0)
        total_weight += w
        weighted_score += r.score * w

        raw_str = f"{r.raw_value:.4f}" if abs(r.raw_value) < 100 else f"{r.raw_value:.2f}"
        score_str = f"{r.score:+.4f}"
        direction = "↑正" if r.direction == "positive" else "↓负"

        print(f"  {r.factor_name:<12} {raw_str:>10} {score_str:>8} {direction:>6} {w:>5.1f}")

    # ── 3. 汇总 ──
    print(f"\n{sep}")
    print(f"  Step 3: 评分汇总")
    print(f"{sep}")

    print(f"  总权重:       {total_weight}")
    print(f"  加权评分:     {weighted_score:+.4f}  (raw score × weight 求和)")
    print(f"  理论范围:     ±{total_weight}")

    # 简易信号判定（与 scoring_engine.py 五档阈值一致）
    if weighted_score >= 3.0:
        signal = "🟢 强烈加仓"
    elif weighted_score >= 1.5:
        signal = "🟢 适度加仓"
    elif weighted_score >= -1.5:
        signal = "🟡 中性/观望"
    elif weighted_score >= -3.0:
        signal = "🔴 适度减仓"
    else:
        signal = "🔴 强烈减仓"

    print(f"  综合信号:     {signal}")
    print(f"\n  ℹ️  注: 此处评分未经截面标准化（需要多只基金截面数据），")
    print(f"     单只基金的 cross_sectional_zscore 因子评分为原始值。")
    print(f"     实际系统中会对基金池内所有基金做截面 Z-score 后再评分。")

    print(f"\n{sep}")
    print(f"  ✅ 回测测试完成")
    print(f"{sep}\n")


if __name__ == "__main__":
    asyncio.run(main())
