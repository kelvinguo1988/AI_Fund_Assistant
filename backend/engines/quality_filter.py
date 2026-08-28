"""第零层：标的质量过滤引擎

介入方式（严格遵守最小改动原则）：
1. 前置否决：棺材钉/心电图形态 + 清盘风险 → 直接剔除候选池
2. 因子修正：动量稳定性修正波动率倒数得分 + 超额持续性调整趋势一致性权重
3. 固定偏置：机构认可度加分/减分
4. 动态阈值：规模冲击 + 仓位漂移 → 上调买入阈值

所有原有因子计算函数（calculate_*）完全不变。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import numpy as np

from backend.data_sources.base import FundData
from backend.engines.factor_engine import FactorScoreResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 全局配置字典（所有阈值集中管理，方便调参）
# ═══════════════════════════════════════════════════════════════════════

QUALITY_CONFIG = {
    # ── 前置否决 ──
    "coffin_nail_consecutive_days": 20,       # 棺材钉：连续N个交易日
    "coffin_nail_max_drawdown_pct": 0.20,     # 棺材钉：期间最大回撤 ≥ 20%
    "coffin_nail_recovery_days": 60,          # 棺材钉：此后N日内未恢复
    "coffin_nail_recovery_pct": 0.90,         # 棺材钉：未恢复到高点的90%
    "coffin_nail_reenter_recovery": 0.85,     # 棺材钉重新准入：回撤修复度 > 0.85

    "ecg_range_low": 0.95,                    # 心电图：净值区间下限
    "ecg_range_high": 1.05,                   # 心电图：净值区间上限
    "ecg_annual_vol_pct": 0.03,               # 心电图：年化波动率 < 3%
    "ecg_spike_pct": 0.02,                    # 心电图：单日涨幅 > 2%
    "ecg_spike_revert_days": 5,               # 心电图：脉冲后N日内跌回
    "ecg_spike_min_count": 3,                 # 心电图：脉冲发生次数 ≥ 3

    "liquidation_shrink_pct": 0.30,           # 清盘风险：单季规模缩减 > 30%
    "liquidation_min_size": 5e7,              # 清盘风险：最新规模 < 5000万元

    # ── 因子修正 ──
    "momentum_stability_weeks": 4,            # 动量稳定性：统计周数
    "momentum_stability_days_per_week": 5,    # 每周交易日数
    "vol_adjust_formula": "score × (0.5 + 0.5 × stability)",
    "excess_windows_days": [21, 63, 126],     # 超额收益：1月/3月/6月窗口
    "trend_consistency_boost_weight": 0.8,    # 超额持续性=1时趋势一致性权重

    # ── 动态阈值（在实际加权评分尺度上，默认总权重6.0） ──
    "base_buy_threshold": 1.5,                # 基础买入阈值（对应"适度加仓"）
    # 卖出阈值 -1.5 必须与 5 档阈值（scoring_thresholds / DEFAULT_THRESHOLDS）的
    # "中性/观望"下界 -1.5 对齐，不得改大（如 -3.0 会吞掉整个 moderate_sell 区间，
    # 这曾是 2026-07-19 的根因：买5/观望28/卖0）。
    # 更深层根因（2026-08-15 复现）：阈值改回 -1.5 后仍 0 卖出——因为当时激活的 7 因子
    # 中 6 个是截面相对 z-score（同池互相抵消≈0），唯一绝对因子 drawdown_recovery 在普涨市
    # 恒为 +1.0（白送 +0.8 地板），trend_consistency(权重0.5) 是唯一可负项（最差 -0.5），
    # 理论最低分≈-0.5，远低于 -1.5 阈值 → 卖出数学上不可达。
    # 治本：激活因子集须含"双向绝对因子"（如 macd_signal 金叉+1.0/死叉-1.0，权重0.5），
    # 让走弱基金的加权分能落到 -1.5 以下。阈值本身保持 -1.5 不变，仅修正因子结构。
    "base_sell_threshold": -1.5,              # 基础卖出阈值（对应"适度减仓"，与 5 档阈值一致）
    "size_shock_buy_increment": 1.0,          # 规模冲击：买入阈值上调量
    "size_shock_growth_pct": 0.50,            # 规模冲击：环比增长 > 50%
    "size_shock_min_size": 1e8,               # 规模冲击：最新规模 ≥ 1亿元
    "drift_buy_increment": 1.0,               # 仓位漂移：买入阈值上调量
    "drift_low_position_pct": 60.0,           # 漂移：任一季度股票仓位 < 60%
    "drift_position_change_pct": 30.0,        # 漂移：相邻两季度仓位变动 ≥ 30%
    "high_purity_min_position_pct": 85.0,     # 高纯度：最低仓位 ≥ 85%
    "high_purity_max_range_pct": 10.0,        # 高纯度：仓位极差 ≤ 10%

    # ── 固定偏置 ──
    "institution_approval_bonus": 0.5,        # 机构认可度：连续上升加分
    "institution_decline_penalty": -0.5,      # 机构认可度：大幅下降减分
    "institution_min_change_pct": 1.0,        # 机构认可度：最小有效变动（百分点）
    "institution_decline_threshold_pct": 2.0, # 机构认可度：惩罚触发阈值
    "insider_growth_bonus": 0.2,              # 内部人增持：额外加分
    "insider_growth_pct": 0.20,               # 内部人增持：增长 > 20%

    # ── 市场环境阈值调节（估值分位来自 MarketRegimeSnapshot）──
    # 极端高估：买入阈值上调（泡沫期谨慎加仓）
    "extreme_high_valuation_pct": 0.85,        # 高估警戒线：估值分位 ≥ 此值触发
    "extreme_high_valuation_buy_increment": 1.0,  # 高估时买入阈值上调量
    # 极端低估：买入阈值下调（便宜时更易触发买入）
    "extreme_low_valuation_pct": 0.15,         # 低估机会线：估值分位 ≤ 此值触发
    "extreme_low_valuation_buy_decrement": 0.5,   # 低估时买入阈值下调量
}


# ═══════════════════════════════════════════════════════════════════════
# 参数元数据（描述 + 分组，供前端配置页展示）
# ═══════════════════════════════════════════════════════════════════════

PARAM_META: dict[str, tuple[str, str]] = {
    # (描述, 分组)
    "coffin_nail_consecutive_days":       ("棺材钉：连续N个交易日",               "前置否决-棺材钉"),
    "coffin_nail_max_drawdown_pct":       ("棺材钉：期间最大回撤阈值",           "前置否决-棺材钉"),
    "coffin_nail_recovery_days":          ("棺材钉：此后N日内未恢复",             "前置否决-棺材钉"),
    "coffin_nail_recovery_pct":           ("棺材钉：未恢复到高点的比例",         "前置否决-棺材钉"),
    "coffin_nail_reenter_recovery":       ("棺材钉重新准入：回撤修复度阈值", "前置否决-棺材钉"),
    "ecg_range_low":                      ("心电图：净值区间下限",               "前置否决-心电图"),
    "ecg_range_high":                     ("心电图：净值区间上限",               "前置否决-心电图"),
    "ecg_annual_vol_pct":                 ("心电图：年化波动率阈值",           "前置否决-心电图"),
    "ecg_spike_pct":                      ("心电图：单日脉冲涨幅阈值",         "前置否决-心电图"),
    "ecg_spike_revert_days":              ("心电图：脉冲后N日内跌回",           "前置否决-心电图"),
    "ecg_spike_min_count":                ("心电图：最小脉冲发生次数",         "前置否决-心电图"),
    "liquidation_shrink_pct":             ("清盘风险：单季规模缩减阈值",       "前置否决-清盘"),
    "liquidation_min_size":               ("清盘风险：最新规模下限（元）",   "前置否决-清盘"),
    "momentum_stability_weeks":           ("动量稳定性：统计周数",             "因子修正"),
    "momentum_stability_days_per_week":   ("动量稳定性：每周交易日数",         "因子修正"),
    "excess_windows_days":                ("超额收益：1月/3月/6月窗口",         "因子修正"),
    "trend_consistency_boost_weight":     ("超额持续性=1时趋势一致性权重", "因子修正"),
    "base_buy_threshold":                 ("基础买入阈值",                             "动态阈值"),
    "base_sell_threshold":                ("基础卖出阈值",                             "动态阈值"),
    "size_shock_buy_increment":           ("规模冲击：买入阈值上调量",         "动态阈值"),
    "size_shock_growth_pct":              ("规模冲击：环比增长阈值",             "动态阈值"),
    "size_shock_min_size":                ("规模冲击：最新规模下限（元）",   "动态阈值"),
    "drift_buy_increment":                ("仓位漂移：买入阈值上调量",         "动态阈值"),
    "drift_low_position_pct":             ("漂移：任一季度股票仓位下限",     "动态阈值"),
    "drift_position_change_pct":          ("漂移：相邻两季度仓位变动阈值", "动态阈值"),
    "high_purity_min_position_pct":       ("高纯度：最低股票仓位",             "动态阈值"),
    "high_purity_max_range_pct":          ("高纯度：仓位极差上限",             "动态阈值"),
    "institution_approval_bonus":         ("机构认可度：连续上升加分",         "固定偏置"),
    "institution_decline_penalty":        ("机构认可度：大幅下降减分",         "固定偏置"),
    "institution_min_change_pct":         ("机构认可度：最小有效变动（百分点）", "固定偏置"),
    "institution_decline_threshold_pct":  ("机构认可度：惩罚触发阈值",     "固定偏置"),
    "insider_growth_bonus":              ("内部人增持：额外加分",                 "固定偏置"),
    "insider_growth_pct":                ("内部人增持：增长比例阈值",         "固定偏置"),
    "extreme_high_valuation_pct":        ("市场环境：高估警戒线（估值分位≥此值触发）", "市场环境阈值"),
    "extreme_high_valuation_buy_increment": ("市场环境：高估时买入阈值上调量",     "市场环境阈值"),
    "extreme_low_valuation_pct":         ("市场环境：低估机会线（估值分位≤此值触发）", "市场环境阈值"),
    "extreme_low_valuation_buy_decrement":  ("市场环境：低估时买入阈值下调量",     "市场环境阈值"),
}


# ═══════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class QualityFilterResult:
    """单只基金的质量过滤结果"""
    fund_code: str
    # ── 前置否决 ──
    vetoed: bool = False
    veto_reason: str = ""
    # ── 因子修正 ──
    momentum_stability: Optional[float] = None     # 动量稳定性 [0,1]
    excess_persistence: int = 0                     # 超额持续性 0/1
    # ── 动态阈值 ──
    dynamic_buy_threshold: float = QUALITY_CONFIG["base_buy_threshold"]
    dynamic_sell_threshold: float = QUALITY_CONFIG["base_sell_threshold"]
    size_shock_triggered: bool = False
    drift_triggered: bool = False
    drift_warning: str = ""
    # ── 市场环境 ──
    extreme_valuation_triggered: bool = False
    market_regime_warning: str = ""
    # ── 固定偏置 ──
    institution_bias: float = 0.0                   # 机构认可度偏置
    # ── 最终输出 ──
    original_score: float = 0.0                     # 原始加权评分（修正前）
    adjusted_score: float = 0.0                     # 修正后评分（含偏置）
    signal_direction: str = "hold"                  # 最终信号
    warnings: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# 1. 前置否决检测函数
# ═══════════════════════════════════════════════════════════════════════

def check_coffin_nail_pattern(
    fund_data: FundData,
    cfg: dict = QUALITY_CONFIG,
) -> bool:
    """检测"棺材钉"形态

    定义：近一年内，存在任意连续20个交易日，期间最大回撤 ≥ 20%，
    且此后60个交易日内净值从未恢复到该高点净值的90%以上。

    Returns: True = 触发否决（应剔除）
    """
    if not fund_data.close_history or len(fund_data.close_history) < 80:
        return False

    prices = np.array(fund_data.close_history[-252:])  # 近一年
    consec = cfg["coffin_nail_consecutive_days"]
    mdd_thresh = cfg["coffin_nail_max_drawdown_pct"]
    recovery_days = cfg["coffin_nail_recovery_days"]
    recovery_pct = cfg["coffin_nail_recovery_pct"]

    n = len(prices)
    # 遍历所有可能的连续20日起点
    for start in range(n - consec):
        window = prices[start:start + consec]
        rolling_max = np.maximum.accumulate(window)
        drawdowns = 1 - window / rolling_max
        max_dd = float(np.max(drawdowns))

        if max_dd >= mdd_thresh:
            # 找到回撤高点
            peak_idx = start + int(np.argmax(rolling_max))
            peak_price = float(prices[peak_idx])

            # 检查崩溃窗口结束后 recovery_days 日内是否恢复
            # 恢复期从20日崩溃窗口结束开始计算（而非从峰值开始）
            recovery_start = start + consec
            end_idx = min(recovery_start + recovery_days, n)
            if end_idx <= recovery_start:
                continue
            future_prices = prices[recovery_start:end_idx]
            if len(future_prices) == 0:
                continue
            max_future = float(np.max(future_prices))
            if max_future < peak_price * recovery_pct:
                logger.info(
                    f"棺材钉形态检测: {fund_data.code}, "
                    f"起点={start}, 回撤={max_dd:.2%}, "
                    f"高点={peak_price:.4f}, 恢复最高={max_future:.4f}"
                )
                return True

    return False


def check_ecg_pattern(
    fund_data: FundData,
    cfg: dict = QUALITY_CONFIG,
) -> bool:
    """检测"心电图"形态

    定义：近一年净值始终在 [0.95, 1.05] 区间内，年化波动率 < 3%，
    且出现过单日涨幅 > 2%后又在5个交易日内跌回区间的脉冲，发生次数 ≥ 3次。

    Returns: True = 触发否决
    """
    if not fund_data.close_history or len(fund_data.close_history) < 252:
        return False

    prices = np.array(fund_data.close_history[-252:])
    returns = np.diff(prices) / prices[:-1]

    # 归一化到 [0.95, 1.05] 区间检查
    normalized = prices / prices[0]
    low = cfg["ecg_range_low"]
    high = cfg["ecg_range_high"]

    # 检查净值是否始终在区间内
    if float(np.min(normalized)) < low or float(np.max(normalized)) > high:
        return False

    # 检查年化波动率
    annual_vol = float(np.std(returns)) * np.sqrt(252)
    if annual_vol >= cfg["ecg_annual_vol_pct"]:
        return False

    # 检查脉冲次数：单日涨幅超阈值后，在 revert_days 内至少回吐一半脉冲涨幅
    # 2026-08-26 修复：原判定 np.min(future_norm) < high 恒为真（前面的区间
    # 检查已保证所有值 ≤ high），"脉冲后跌回"校验形同虚设。改为要求脉冲后
    # 净值至少回落到脉冲涨幅一半的位置，才是真正的"心电图式脉冲回吐"。
    spike_pct = cfg["ecg_spike_pct"]
    revert_days = cfg["ecg_spike_revert_days"]
    spike_count = 0

    for i in range(len(returns)):
        if returns[i] <= spike_pct:
            continue
        pre_close = prices[i]
        spike_close = prices[i + 1]
        end_idx = min(i + 1 + revert_days, len(prices))
        future = prices[i + 2:end_idx]
        if len(future) > 0:
            revert_level = pre_close + 0.5 * (spike_close - pre_close)
            if float(np.min(future)) <= revert_level:
                spike_count += 1

    if spike_count >= cfg["ecg_spike_min_count"]:
        logger.info(
            f"心电图形态检测: {fund_data.code}, "
            f"年化波动率={annual_vol:.4f}, 脉冲次数={spike_count}"
        )
        return True

    return False


def check_liquidation_risk(
    quarterly_history: list[dict],
    today: date = None,
    cfg: dict = QUALITY_CONFIG,
) -> bool:
    """检测清盘风险

    条件：连续两个季报规模环比缩减均 > 30%，且最新规模 < 5000万元。

    Returns: True = 触发否决
    """
    if today is None:
        today = date.today()

    # 筛选已生效的季度数据
    active_records = _get_active_quarterly_records(quarterly_history, today)
    if len(active_records) < 2:
        return False

    # 取最近两条
    latest = active_records[-1]
    prev = active_records[-2]

    latest_size = latest.get("fund_size")
    prev_size = prev.get("fund_size")

    if latest_size is None or prev_size is None or prev_size <= 0:
        return False

    # 再取前一条计算连续两个季度
    if len(active_records) >= 3:
        prev_prev = active_records[-3]
        prev_prev_size = prev_prev.get("fund_size")
        if prev_prev_size and prev_prev_size > 0:
            shrink1 = (prev_prev_size - prev_size) / prev_prev_size
            shrink2 = (prev_size - latest_size) / prev_size
            if (shrink1 > cfg["liquidation_shrink_pct"]
                    and shrink2 > cfg["liquidation_shrink_pct"]
                    and latest_size < cfg["liquidation_min_size"]):
                logger.info(
                    f"清盘风险: 连续缩减 {shrink1:.1%} + {shrink2:.1%}, "
                    f"最新规模={latest_size:.0f}"
                )
                return True

    # 不足三条季报时无法判断"连续两个季报"缩减，保守放行

    return False


# ═══════════════════════════════════════════════════════════════════════
# 2. 衍生因子计算函数
# ═══════════════════════════════════════════════════════════════════════

def calc_momentum_stability(
    fund_data: FundData,
    cfg: dict = QUALITY_CONFIG,
) -> Optional[float]:
    """计算动量稳定性

    取最近4个交易周（20个交易日），计算每周收益率，
    统计周收益 > 0 的周数占比，值域[0,1]。

    Returns: 动量稳定性值，数据不足返回 None
    """
    weeks = cfg["momentum_stability_weeks"]
    days_per_week = cfg["momentum_stability_days_per_week"]
    total_days = weeks * days_per_week  # 20

    if not fund_data.close_history or len(fund_data.close_history) < total_days + 1:
        return None

    prices = np.array(fund_data.close_history[-(total_days + 1):])
    positive_weeks = 0

    for w in range(weeks):
        start_idx = w * days_per_week
        end_idx = (w + 1) * days_per_week
        week_return = prices[end_idx] / prices[start_idx] - 1
        if week_return > 0:
            positive_weeks += 1

    stability = positive_weeks / weeks
    return round(stability, 4)


def calc_excess_return_persistence(
    fund_data: FundData,
    cfg: dict = QUALITY_CONFIG,
) -> int:
    """计算超额收益持续性

    计算基金相对业绩基准的近1月、3月、6月超额收益。
    三个窗口超额均 > 0 则值为1，否则为0。

    Returns: 0 或 1
    """
    if (not fund_data.close_history or not fund_data.benchmark_history
            or len(fund_data.close_history) < 130
            or len(fund_data.benchmark_history) < 130):
        return 0

    from backend.engines.factor_engine import align_price_series
    fund_prices, bench_prices = align_price_series(fund_data)
    min_len = len(fund_prices)

    windows = cfg["excess_windows_days"]  # [21, 63, 126]
    all_positive = True

    for w in windows:
        if min_len < w + 1:
            return 0
        fund_ret = fund_prices[-1] / fund_prices[-w] - 1
        bench_ret = bench_prices[-1] / bench_prices[-w] - 1
        excess = fund_ret - bench_ret
        if excess <= 0:
            all_positive = False
            break

    return 1 if all_positive else 0


# ═══════════════════════════════════════════════════════════════════════
# 3. 动态阈值计算函数
# ═══════════════════════════════════════════════════════════════════════

def check_size_shock(
    quarterly_history: list[dict],
    today: date = None,
    cfg: dict = QUALITY_CONFIG,
) -> bool:
    """检查规模冲击警告

    条件：最新季报规模相比上季报环比增长 > 50%，且最新规模 ≥ 1亿元。

    Returns: True = 触发规模冲击
    """
    if today is None:
        today = date.today()

    active_records = _get_active_quarterly_records(quarterly_history, today)
    if len(active_records) < 2:
        return False

    latest = active_records[-1]
    prev = active_records[-2]

    latest_size = latest.get("fund_size")
    prev_size = prev.get("fund_size")

    if latest_size is None or prev_size is None or prev_size <= 0:
        return False

    growth = (latest_size - prev_size) / prev_size
    if growth > cfg["size_shock_growth_pct"] and latest_size >= cfg["size_shock_min_size"]:
        logger.info(
            f"规模冲击: 环比增长 {growth:.1%}, 最新规模={latest_size:.0f}"
        )
        return True

    return False


def check_allocation_drift(
    quarterly_history: list[dict],
    today: date = None,
    cfg: dict = QUALITY_CONFIG,
) -> tuple[bool, bool]:
    """检查资产配置纯度

    Returns: (is_drift, is_high_purity)
    - is_drift: 漂移型（买入阈值上调）
    - is_high_purity: 高纯度（无调节）
    """
    if today is None:
        today = date.today()

    active_records = _get_active_quarterly_records(quarterly_history, today)
    # 取最近4个季报
    recent = [r for r in active_records if r.get("stock_position_ratio") is not None][-4:]

    if len(recent) < 4:
        return False, False

    positions = [r["stock_position_ratio"] for r in recent]
    min_pos = min(positions)
    max_pos = max(positions)

    # 高纯度检查
    is_high_purity = (
        min_pos >= cfg["high_purity_min_position_pct"]
        and (max_pos - min_pos) <= cfg["high_purity_max_range_pct"]
    )

    # 漂移检查
    is_drift = False

    # 条件1: 任一季度股票仓位 < 60%
    if any(p < cfg["drift_low_position_pct"] for p in positions):
        is_drift = True

    # 条件2: 相邻两季度仓位变动 ≥ 30%
    if not is_drift:
        for i in range(1, len(positions)):
            change = abs(positions[i] - positions[i - 1])
            if change >= cfg["drift_position_change_pct"]:
                is_drift = True
                break

    if is_drift:
        logger.info(f"仓位漂移: 仓位序列={positions}")

    return is_drift, is_high_purity


# ═══════════════════════════════════════════════════════════════════════
# 4. 固定偏置计算函数
# ═══════════════════════════════════════════════════════════════════════

def calc_institution_approval(
    quarterly_history: list[dict],
    today: date = None,
    cfg: dict = QUALITY_CONFIG,
) -> float:
    """计算机构认可度偏置分

    加分条件：最近两个报告期机构持有比例均环比上升，且每次 ≥ 1个百分点 → +bonus
    惩罚条件：最新报告期机构占比下降 ≥ 2个百分点 → penalty
    可选叠加：内部人持有份额较上期增长 > 20% → +insider_bonus

    Returns: 偏置分值（+bonus / 0 / penalty）
    """
    if today is None:
        today = date.today()

    active_records = _get_active_quarterly_records(quarterly_history, today)

    # 筛选有机构持有数据的记录
    inst_records = [
        r for r in active_records
        if r.get("institution_holding_ratio") is not None
    ]

    bias = 0.0

    if len(inst_records) >= 2:
        latest_ratio = inst_records[-1]["institution_holding_ratio"]
        prev_ratio = inst_records[-2]["institution_holding_ratio"]

        # 检查最近两期是否都上升
        if len(inst_records) >= 3:
            prev_prev_ratio = inst_records[-3]["institution_holding_ratio"]
            change1 = prev_ratio - prev_prev_ratio
            change2 = latest_ratio - prev_ratio

            min_change = cfg["institution_min_change_pct"]
            if change1 >= min_change and change2 >= min_change:
                bias = cfg["institution_approval_bonus"]
                logger.info(f"机构认可度加分: +{bias}")

            decline_thresh = cfg["institution_decline_threshold_pct"]
            if change2 <= -decline_thresh:
                bias = cfg["institution_decline_penalty"]
                logger.info(f"机构认可度惩罚: {bias}")
        else:
            # 只有两期，只看最新变动
            change = latest_ratio - prev_ratio
            decline_thresh = cfg["institution_decline_threshold_pct"]
            if change <= -decline_thresh:
                bias = cfg["institution_decline_penalty"]
    else:
        if inst_records:
            logger.info(f"机构持有数据不足（仅{len(inst_records)}期），偏置记为0")
        else:
            logger.info("缺失机构持有数据，偏置记为0")

    # 内部人增持叠加
    insider_records = [
        r for r in active_records
        if r.get("insider_holding_shares") is not None
    ]
    if len(insider_records) >= 2:
        latest_insider = insider_records[-1]["insider_holding_shares"]
        prev_insider = insider_records[-2]["insider_holding_shares"]
        if prev_insider and prev_insider > 0:
            growth = (latest_insider - prev_insider) / prev_insider
            if growth > cfg["insider_growth_pct"]:
                insider_bonus = cfg["insider_growth_bonus"]
                bias += insider_bonus
                logger.info(f"内部人增持叠加: +{insider_bonus}")

    return bias


# ═══════════════════════════════════════════════════════════════════════
# 5. 因子修正函数（外围介入，不修改原计算函数）
# ═══════════════════════════════════════════════════════════════════════

def apply_factor_corrections(
    factor_scores: list[FactorScoreResult],
    active_factors: list[dict],
    momentum_stability: Optional[float],
    excess_persistence: int,
    cfg: dict = QUALITY_CONFIG,
) -> tuple[list[FactorScoreResult], list[float]]:
    """在原有因子得分产出后、加权求和前，做外围修正

    修正1: 波动率倒数得分 × (0.5 + 0.5 × 动量稳定性)
    修正2: 趋势一致性权重从 0.5 临时提升至 0.8

    原有因子计算函数完全不变，仅对结果做外部修饰。

    Args:
        factor_scores: 原 calculate_all 返回的因子评分列表
        active_factors: 活跃因子配置（含 weight）
        momentum_stability: 动量稳定性 [0,1]，None 时跳过修正
        excess_persistence: 超额持续性 0/1

    Returns:
        (修正后的 factor_scores, 修正后的权重列表)
    """
    corrected_scores = list(factor_scores)
    weights = [f.get("weight", 1.0) for f in active_factors]
    corrected_weights = list(weights)

    # 找到因子索引
    inv_vol_idx = None
    trend_idx = None
    for i, f in enumerate(active_factors):
        code = f.get("code", "")
        if code == "inv_volatility":
            inv_vol_idx = i
        elif code == "trend_consistency":
            trend_idx = i

    # 修正1: 波动率倒数截面标准化后的离散分 × (0.5 + 0.5 × 动量稳定性)
    # 注意：此修正实际作用于截面标准化之后的分数（-1~+1），而非原始 inv_vol，
    # 效果是"动量不稳定的基金正分被压向 0"，只缩幅不翻向。
    if inv_vol_idx is not None and momentum_stability is not None:
        original_score = corrected_scores[inv_vol_idx].score
        multiplier = 0.5 + 0.5 * momentum_stability
        new_score = original_score * multiplier
        corrected_scores[inv_vol_idx] = FactorScoreResult(
            factor_code=corrected_scores[inv_vol_idx].factor_code,
            factor_name=corrected_scores[inv_vol_idx].factor_name,
            raw_value=corrected_scores[inv_vol_idx].raw_value,
            score=round(new_score, 6),
            direction=corrected_scores[inv_vol_idx].direction,
        )
        logger.info(
            f"波动率倒数修正: {original_score:.4f} × {multiplier:.4f} = {new_score:.4f}"
        )

    # 修正2: 趋势一致性权重提升（仅取用户配置与 boost 的较大值，不覆盖更高配置）
    # 2026-08-22 审计修复：原 corrected_weights[trend_idx] = cfg[boost] 固定 0.8
    # 会把用户配置的 1.5 反向降到 0.8；改为 max(original, boost) 保留用户更高配置
    if trend_idx is not None and excess_persistence == 1:
        trend_score = corrected_scores[trend_idx].score
        # 仅当趋势一致性信号为正向（score == 1.0）时提升权重
        if trend_score >= 1.0:
            original_weight = corrected_weights[trend_idx]
            boost_weight = cfg["trend_consistency_boost_weight"]
            corrected_weights[trend_idx] = max(original_weight, boost_weight)
            logger.info(
                f"趋势一致性权重提升: {original_weight} → {max(original_weight, boost_weight)}"
            )

    return corrected_scores, corrected_weights


# ═══════════════════════════════════════════════════════════════════════
# 6. 动态阈值决策函数
# ═══════════════════════════════════════════════════════════════════════

_SENTINEL = object()  # 区分"未传参"（回退全局）与"显式传 None"（无快照）


def _resolve_regime(regime_snapshot=_SENTINEL):
    """解析市场环境快照：显式传入优先（含 None=无数据），未传时回退模块级全局"""
    if regime_snapshot is not _SENTINEL:
        return regime_snapshot
    from backend.engines.factor_engine import get_current_regime
    return get_current_regime()


def compute_dynamic_thresholds(
    size_shock: bool,
    drift: bool,
    cfg: dict = QUALITY_CONFIG,
    regime_snapshot=_SENTINEL,
) -> tuple[float, float]:
    """计算每只基金的专属买入/卖出阈值

    buy_threshold = base + (size_shock ? increment : 0) + (drift ? increment : 0)
                  + 市场环境调节（极端高估上调 / 极端低估下调）
    sell_threshold = base_sell（固定不变，与五档阈值"中性/观望"下界对齐）

    Args:
        regime_snapshot: 市场环境快照（任务隔离传递；None 时回退模块级全局）

    Returns: (buy_threshold, sell_threshold)
    """
    buy = cfg["base_buy_threshold"]
    sell = cfg["base_sell_threshold"]

    if size_shock:
        buy += cfg["size_shock_buy_increment"]
    if drift:
        buy += cfg["drift_buy_increment"]

    # 市场环境调节：快照缺失/分位缺失时不调节
    regime = _resolve_regime(regime_snapshot)
    pct = getattr(regime, "valuation_percentile", None) if regime is not None else None
    if pct is not None:
        if pct >= cfg["extreme_high_valuation_pct"]:
            buy += cfg["extreme_high_valuation_buy_increment"]
        elif pct <= cfg["extreme_low_valuation_pct"]:
            buy -= cfg["extreme_low_valuation_buy_decrement"]
            # 低估下调不把买入阈值打到 0 以下（避免白送买入信号）
            buy = max(buy, 0.5)

    return buy, sell


def determine_signal(
    adjusted_score: float,
    buy_threshold: float,
    sell_threshold: float,
    drift_triggered: bool,
    cfg: dict = QUALITY_CONFIG,
) -> tuple[str, str, str]:
    """基于动态阈值判定最终信号

    Args:
        adjusted_score: 修正后的最终评分（含偏置）
        buy_threshold: 动态买入阈值
        sell_threshold: 卖出阈值（固定）
        drift_triggered: 是否触发漂移标签

    Returns:
        (signal_direction, signal_strength, warning_text)
    """
    warnings = []

    if adjusted_score >= buy_threshold:
        direction = "buy"
        if adjusted_score >= buy_threshold + 1.5:
            strength = "heavy_buy"
        else:
            strength = "moderate_buy"
        if drift_triggered:
            warning = "警告：该基金仓位择时成分显著，信号可能不稳定，请人工复核。"
            warnings.append(warning)
    elif adjusted_score <= sell_threshold:
        direction = "sell"
        if adjusted_score <= sell_threshold - 1.5:
            strength = "heavy_sell"
        else:
            strength = "moderate_sell"
    else:
        direction = "hold"
        strength = "hold"

    return direction, strength, "; ".join(warnings)


# ═══════════════════════════════════════════════════════════════════════
# 7. 质量过滤器主类
# ═══════════════════════════════════════════════════════════════════════

class QualityFilter:
    """标的质量过滤器 — 第零层

    使用方式：
        qf = QualityFilter()

        # Step 1: 前置否决
        vetoed, reason = qf.pre_filter(fund_data, quarterly_history)

        # Step 2: 计算衍生因子
        stability = qf.calc_momentum_stability(fund_data)
        excess = qf.calc_excess_persistence(fund_data)

        # Step 3: 因子修正（在加权求和前）
        corrected_scores, corrected_weights = qf.apply_corrections(
            factor_scores, active_factors, stability, excess)

        # Step 4: 计算加权评分（用原有方式）
        raw_score = sum(s.score * w for s, w in zip(corrected_scores, corrected_weights))

        # Step 5: 偏置加分
        bias = qf.calc_bias(quarterly_history)
        final_score = raw_score + bias

        # Step 6: 动态阈值决策
        buy_th, sell_th = qf.get_thresholds(size_shock, drift)
        direction, strength, warning = qf.decide(final_score, buy_th, sell_th, drift)
    """

    def __init__(self, config: dict = None) -> None:
        self.cfg = config or QUALITY_CONFIG

    def pre_filter(
        self,
        fund_data: FundData,
        quarterly_history: list[dict],
        today: date = None,
    ) -> tuple[bool, str]:
        """前置否决检测

        Returns: (是否被否决, 否决原因)
        """
        if today is None:
            today = date.today()

        # 1. 棺材钉形态
        if check_coffin_nail_pattern(fund_data, self.cfg):
            # 检查是否已重新创新高且回撤修复度 > 0.85
            if self._check_recovery_from_coffin(fund_data):
                return False, ""
            return True, "棺材钉形态：近一年存在严重回撤且未恢复"

        # 2. 心电图形态
        if check_ecg_pattern(fund_data, self.cfg):
            return True, "心电图形态：净值异常平稳伴随机脉冲"

        # 3. 清盘风险
        if check_liquidation_risk(quarterly_history, today, self.cfg):
            return True, "清盘风险：规模持续缩减且低于5000万元"

        return False, ""

    def _check_recovery_from_coffin(self, fund_data: FundData) -> bool:
        """检查棺材钉后是否已恢复（创新高 + 回撤修复度 > 0.85）"""
        if not fund_data.close_history or len(fund_data.close_history) < 60:
            return False

        prices = np.array(fund_data.close_history)
        rolling_max = float(np.max(prices))
        current = float(prices[-1])
        recovery = current / rolling_max if rolling_max > 0 else 0

        # 当前净值就是最高点（创新高）且修复度 > 0.85
        return recovery > self.cfg["coffin_nail_reenter_recovery"] and current >= rolling_max * 0.99

    def calc_momentum_stability(self, fund_data: FundData) -> Optional[float]:
        """计算动量稳定性"""
        return calc_momentum_stability(fund_data, self.cfg)

    def calc_excess_persistence(self, fund_data: FundData) -> int:
        """计算超额收益持续性"""
        return calc_excess_return_persistence(fund_data, self.cfg)

    def apply_corrections(
        self,
        factor_scores: list[FactorScoreResult],
        active_factors: list[dict],
        momentum_stability: Optional[float],
        excess_persistence: int,
    ) -> tuple[list[FactorScoreResult], list[float]]:
        """因子修正（外围介入）"""
        return apply_factor_corrections(
            factor_scores, active_factors,
            momentum_stability, excess_persistence, self.cfg,
        )

    def calc_bias(
        self,
        quarterly_history: list[dict],
        today: date = None,
    ) -> float:
        """计算固定偏置"""
        return calc_institution_approval(quarterly_history, today, self.cfg)

    def get_thresholds(
        self,
        size_shock: bool,
        drift: bool,
    ) -> tuple[float, float]:
        """计算动态阈值"""
        return compute_dynamic_thresholds(size_shock, drift, self.cfg)

    def decide(
        self,
        adjusted_score: float,
        buy_threshold: float,
        sell_threshold: float,
        drift_triggered: bool,
    ) -> tuple[str, str, str]:
        """动态阈值决策"""
        return determine_signal(
            adjusted_score, buy_threshold, sell_threshold,
            drift_triggered, self.cfg,
        )

    def check_size_shock(self, quarterly_history: list[dict], today: date = None) -> bool:
        """规模冲击检测"""
        return check_size_shock(quarterly_history, today or date.today(), self.cfg)

    def check_drift(self, quarterly_history: list[dict], today: date = None) -> tuple[bool, bool]:
        """资产配置纯度检测 → (is_drift, is_high_purity)"""
        return check_allocation_drift(quarterly_history, today or date.today(), self.cfg)

    def build_result(
        self,
        fund_code: str,
        fund_data: FundData,
        quarterly_history: list[dict],
        factor_scores: list[FactorScoreResult],
        active_factors: list[dict],
        today: date = None,
        regime_snapshot=_SENTINEL,
    ) -> tuple[QualityFilterResult, list[FactorScoreResult], list[float]]:
        """一站式处理：前置否决→衍生因子→修正→阈值→偏置→决策

        Returns:
            (QualityFilterResult, corrected_factor_scores, corrected_weights)
            如果被否决，corrected 参数返回原始值
        """
        if today is None:
            today = date.today()

        result = QualityFilterResult(fund_code=fund_code)

        # Step 1: 前置否决
        vetoed, reason = self.pre_filter(fund_data, quarterly_history, today)
        if vetoed:
            result.vetoed = True
            result.veto_reason = reason
            weights = [f.get("weight", 1.0) for f in active_factors]
            return result, factor_scores, weights

        # Step 2: 衍生因子
        result.momentum_stability = self.calc_momentum_stability(fund_data)
        result.excess_persistence = self.calc_excess_persistence(fund_data)

        # Step 3: 动态阈值（含市场环境调节）
        result.size_shock_triggered = self.check_size_shock(quarterly_history, today)
        drift, _ = self.check_drift(quarterly_history, today)
        result.drift_triggered = drift
        result.dynamic_buy_threshold, result.dynamic_sell_threshold = compute_dynamic_thresholds(
            result.size_shock_triggered, drift, self.cfg, regime_snapshot
        )

        if drift:
            result.drift_warning = "警告：该基金仓位择时成分显著，信号可能不稳定，请人工复核。"
            result.warnings.append(result.drift_warning)

        # 极端市场环境提示（估值分位触发阈值调节时）
        regime = _resolve_regime(regime_snapshot)
        pct = getattr(regime, "valuation_percentile", None) if regime is not None else None
        if pct is not None:
            if pct >= self.cfg["extreme_high_valuation_pct"]:
                result.extreme_valuation_triggered = True
                result.market_regime_warning = (
                    f"提示：大盘估值分位 {pct:.0%} 处于历史高位，买入阈值已上调 "
                    f"{self.cfg['extreme_high_valuation_buy_increment']}，请谨慎追高。"
                )
                result.warnings.append(result.market_regime_warning)
            elif pct <= self.cfg["extreme_low_valuation_pct"]:
                result.extreme_valuation_triggered = True
                result.market_regime_warning = (
                    f"提示：大盘估值分位 {pct:.0%} 处于历史低位，买入阈值已下调 "
                    f"{self.cfg['extreme_low_valuation_buy_decrement']}，左侧布局窗口。"
                )
                result.warnings.append(result.market_regime_warning)

        # Step 4: 因子修正
        corrected_scores, corrected_weights = self.apply_corrections(
            factor_scores, active_factors,
            result.momentum_stability, result.excess_persistence,
        )

        # Step 5: 偏置
        result.institution_bias = self.calc_bias(quarterly_history, today)

        return result, corrected_scores, corrected_weights


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _get_active_quarterly_records(
    quarterly_history: list[dict],
    today: date,
) -> list[dict]:
    """筛选已生效的季度数据（考虑报告发布滞后）

    例如：3月31日的季报，假设4月底发布，5月第一个交易日生效。
    只有 effective_date <= today 的记录才可使用，严禁未来数据泄露。
    """
    today_str = today.isoformat()
    active = [
        r for r in quarterly_history
        if r.get("effective_date", "9999-12-31") <= today_str
    ]
    # 按 report_date 排序
    active.sort(key=lambda r: r.get("report_date", ""))
    return active


# 全局实例
quality_filter = QualityFilter()


# ═══════════════════════════════════════════════════════════════════════
# DB 配置读取（供 API 路由和分析服务使用）
# ═══════════════════════════════════════════════════════════════════════

_QUALITY_CONFIG_DB_KEY = "quality_filter_config"


async def merge_quality_config(db) -> dict:
    """从 SystemConfig 表读取配置并与硬编码默认值合并

    DB 值覆盖默认值；未设置的参数保持默认。
    """
    import json as _json
    from sqlalchemy import select
    from backend.models.system_config import SystemConfig

    merged = dict(QUALITY_CONFIG)  # 复制默认值

    result = await db.execute(
        select(SystemConfig).where(SystemConfig.config_key == _QUALITY_CONFIG_DB_KEY)
    )
    config_row = result.scalars().first()
    if config_row and config_row.config_value:
        try:
            overrides = _json.loads(config_row.config_value)
            if isinstance(overrides, dict):
                for k, v in overrides.items():
                    # 2026-08-22 审计修复：放宽类型校验，支持 list/str/int/float
                    # 原校验 isinstance(v, (int, float)) 导致 excess_windows_days(list)
                    # 和 vol_adjust_formula(str) 无法从 DB 覆盖
                    if k in merged:
                        merged[k] = v
        except (ValueError, TypeError):
            logger.warning("质量过滤配置 JSON 解析失败，使用默认值")

    return merged


def build_quality_filter(merged_config: dict) -> QualityFilter:
    """用合并后的配置创建 QualityFilter 实例"""
    return QualityFilter(config=merged_config)
