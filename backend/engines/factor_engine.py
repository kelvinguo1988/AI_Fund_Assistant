"""因子计算引擎 — 8 因子 + 信号规则 + 截面标准化

因子列表（来自 README_1.md 配置体系）：
1. 价格百分位 (price_percentile)  — 负向, 权重 1.2
2. 股债性价比FED (fed_model)      — 正向, 权重 1.2
3. 动量因子 (momentum_6m)        — 正向, 权重 1.0
4. 波动率倒数 (inv_volatility)    — 正向, 权重 0.8
5. 信息比率 (info_ratio)          — 正向, 权重 0.8
6. MACD信号 (macd_signal)        — 正向, 权重 0.5
7. 最大回撤 (max_drawdown)       — 正向, 权重 0.5
8. 规模稳定性 (size_stability)    — 正向, 权重 0.4

分值范围: -1.0 ~ +1.0（每因子），加权求和 → -6.4 ~ +6.4
"""

import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from backend.data_sources.base import FundData

logger = logging.getLogger(__name__)


@dataclass
class FactorScoreResult:
    """因子评分结果"""
    factor_code: str
    factor_name: str
    raw_value: float      # 原始计算值
    score: float          # -1.0 ~ +1.0 标准化评分
    direction: str        # positive / negative


# ═══════════════════════════════════════════════════════════════════════
# 公式工具函数
# ═══════════════════════════════════════════════════════════════════════

def ema(data: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均"""
    if len(data) < period:
        return np.array([float(np.mean(data))] * len(data))
    multiplier = 2.0 / (period + 1)
    result = np.zeros_like(data, dtype=float)
    result[period - 1] = float(np.mean(data[:period]))
    for i in range(period, len(data)):
        result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1]
    result[:period - 1] = result[period - 1]
    return result


def percentile_rank(value: float, history: np.ndarray) -> float:
    """计算 value 在 history 中的百分位排名 (0~1)"""
    if len(history) == 0:
        return 0.5
    return float(np.sum(history <= value)) / len(history)


def align_price_series(fund_data: FundData) -> tuple[np.ndarray, np.ndarray]:
    """按日期交集对齐基金净值与基准指数序列

    基准（沪深300）是全交易日序列，基金净值存在停牌缺日/新基金历史短，
    按索引对齐会在错位日产生虚假超额收益（info_ratio / 超额持续性失真）。
    任一侧日期序列缺失或长度不匹配时，退化为尾部等长对齐（旧行为，兼容旧调用方）。

    Returns:
        (fund_prices, bench_prices)：同长度、同日期序的两个价格序列
    """
    fund_prices = np.array(fund_data.close_history, dtype=float)
    bench_prices = np.array(fund_data.benchmark_history, dtype=float)

    fund_dates = fund_data.date_history or []
    bench_dates = getattr(fund_data, "benchmark_date_history", None) or []
    if (
        fund_dates and bench_dates
        and len(fund_dates) == len(fund_prices)
        and len(bench_dates) == len(bench_prices)
    ):
        bench_map = dict(zip(bench_dates, bench_prices))
        pairs = [
            (p, bench_map[d])
            for d, p in zip(fund_dates, fund_prices)
            if d in bench_map
        ]
        if len(pairs) >= 2:
            if len(pairs) < len(fund_prices):
                logger.info(
                    f"基准日期对齐: {len(fund_prices)} → {len(pairs)} 行 "
                    f"(code={fund_data.code})"
                )
            return (
                np.array([a for a, _ in pairs]),
                np.array([b for _, b in pairs]),
            )

    min_len = min(len(fund_prices), len(bench_prices))
    return fund_prices[-min_len:], bench_prices[-min_len:]


# ═══════════════════════════════════════════════════════════════════════
# 信号规则评估
# ═══════════════════════════════════════════════════════════════════════

def _parse_simple(value: float, cond: str) -> bool:
    """解析简单比较条件 (<=, >=, <, >, ==, !=)"""
    cond = cond.strip()
    if cond.startswith(">="):
        return value >= float(cond[2:].strip())
    if cond.startswith("<="):
        return value <= float(cond[2:].strip())
    if cond.startswith(">"):
        return value > float(cond[1:].strip())
    if cond.startswith("<"):
        return value < float(cond[1:].strip())
    if cond.startswith("=="):
        return value == float(cond[2:].strip())
    if cond.startswith("!="):
        return value != float(cond[2:].strip())
    return False


def _evaluate_rule(value: float, condition: str) -> bool:
    """评估一条规则条件"""
    cond = condition.strip()
    if cond == "else":
        return True
    if " and " in cond:
        parts = cond.split(" and ")
        return all(_parse_simple(value, p) for p in parts)
    if " or " in cond:
        parts = cond.split(" or ")
        return any(_parse_simple(value, p) for p in parts)
    return _parse_simple(value, cond)


def evaluate_signal_rules(raw_value: float, rules: list[dict]) -> float:
    """按顺序匹配信号规则，返回首发匹配的得分

    Args:
        raw_value: 原始计算值
        rules: 信号规则数组 [{"condition":"<= 0.2","score":1.0}, ...]

    Returns:
        -1.0 ~ +1.0 得分；无匹配则返回 0.0
    """
    if not rules:
        return raw_value  # 无规则时值本身即为得分（后续可能做截面标准化）
    for rule in rules:
        if _evaluate_rule(raw_value, rule.get("condition", "")):
            return float(rule["score"])
    return 0.0


# ═══════════════════════════════════════════════════════════════════════
# 截面标准化
# ═══════════════════════════════════════════════════════════════════════

def apply_cross_sectional_zscore(
    scores: dict[str, float],
    thresholds: Optional[list[float]] = None,
) -> dict[str, float]:
    """截面 Z-score 标准化 → -1~+1 映射（五档对称）

    Args:
        scores: {fund_code: pre_norm_score}
        thresholds: [upper, mid_upper, mid_lower, lower] 默认 [1.0, 0.5, -0.5, -1.0]

    Returns:
        {fund_code: normalized_score}
    """
    values = np.array(list(scores.values()))
    if len(values) < 2 or np.std(values) == 0:
        return {k: 0.0 for k in scores}

    mean = float(np.mean(values))
    std = float(np.std(values))
    t = thresholds or [1.0, 0.5, -0.5, -1.0]

    # 兼容旧版 3 阈值 → 扩展为 4 阈值
    if len(t) == 3:
        t = [t[0], (t[0] + t[1]) / 2, (t[1] + t[2]) / 2, t[2]]

    return {
        code: 1.0 if z > t[0] else 0.5 if z > t[1] else 0.0 if z > t[2] else -0.5 if z > t[3] else -1.0
        for code, z in ((code, (val - mean) / std) for code, val in scores.items())
    }


# ═══════════════════════════════════════════════════════════════════════
# 7 个因子计算函数
# ═══════════════════════════════════════════════════════════════════════

def calculate_price_percentile(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """价格百分位 — 负向（低估值得分高）

    2026-08-22 审计修复：原名 pe_percentile 名实不符（实际用 close_history 算价格
    百分位，非 PE 百分位）。改为 price_percentile 消除歧义。基金场景"净值低位
    ≈便宜"勉强成立，但牛市中净值新高≠高估，需结合其他因子判断。
    用当前价格对比历史价格序列，近似判断估值高低：
    价格处于历史低位 → 大概率低估 → 高分。
    公式: percentile_rank(close, close_history)
    信号: ≤0.2→1.0, ≤0.4→0.5, ≤0.6→0, ≤0.8→-0.5, >0.8→-1.0
    """
    window = (params or {}).get("window", 1250)

    current_close = fund_data.close
    if current_close is None:
        # 2026-08-26 修复：原代码回退用 PE 对净值历史算百分位——量纲差一个数量级
        # （PE≈20 vs 净值≈1.5），pct 恒为 1.0，该基金永远拿 -1.0 垃圾分。
        # close 缺失时直接返回中性分。
        logger.warning(f"价格百分位数据不足 code={fund_data.code}")
        return FactorScoreResult("price_percentile", "价格百分位", 0.0, 0.0, "negative")

    history = np.array(fund_data.close_history[-window:]) if fund_data.close_history else np.array([current_close])
    pct = percentile_rank(current_close, history)

    rules = [
        {"condition": "<= 0.2", "score": 1.0},
        {"condition": "<= 0.4", "score": 0.5},
        {"condition": "<= 0.6", "score": 0.0},
        {"condition": "<= 0.8", "score": -0.5},
        {"condition": "> 0.8", "score": -1.0},
    ]
    score = evaluate_signal_rules(pct, rules)
    return FactorScoreResult("price_percentile", "价格百分位", round(pct, 4), score, "negative")


def calculate_fed_model(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """股债性价比 FED — 正向

    FED = (1/PE) × 100 - 10Y_bond_yield（%）
    A 股经验阈值（沪深300）：
      FED > 5%  → 极具性价比 → 1.0
      FED > 3%  → 有性价比   → 0.5
      FED > 1%  → 中性       → 0.0
      FED > -1% → 偏贵       → -0.5
      FED ≤ -1% → 很贵       → -1.0
    """
    if fund_data.pe is None or fund_data.pe <= 0:
        logger.warning(f"FED模型数据不足 code={fund_data.code}")
        return FactorScoreResult("fed_model", "股债性价比FED", 0.0, 0.0, "positive")

    earnings_yield = 1.0 / fund_data.pe * 100
    # 2026-08-26 修复：原代码 bond_yield 缺失时静默填 2.5 —— 实际利率偏离时
    # 0.5pp 误差足以翻转一档信号。与数据源侧"失败返 None 不造假数据"策略
    # 保持一致：缺失时返回中性分。
    bond = fund_data.bond_yield
    if bond is None:
        logger.warning(f"股债性价比FED无债券收益率数据 code={fund_data.code}，返回中性")
        return FactorScoreResult("fed_model", "股债性价比FED", 0.0, 0.0, "positive")
    fed_value = earnings_yield - bond

    rules = [
        {"condition": "> 5", "score": 1.0},
        {"condition": "> 3", "score": 0.5},
        {"condition": "> 1", "score": 0.0},
        {"condition": "> -1", "score": -0.5},
        {"condition": "else", "score": -1.0},
    ]
    score = evaluate_signal_rules(fed_value, rules)
    return FactorScoreResult("fed_model", "股债性价比FED", round(fed_value, 4), score, "positive")


def calculate_momentum_6m(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """动量因子 — 正向

    公式: (nav/shift(nav,126)-1) / (std(returns,126)*sqrt(126))
    信号: >1.0→1.0, >0.5→0.5, 中间→0, <-0.5→-0.5, <-1.0→-1.0
    """
    window = (params or {}).get("window", 126)

    if not fund_data.close_history or len(fund_data.close_history) < window + 10:
        logger.warning(f"动量因子数据不足 code={fund_data.code}")
        return FactorScoreResult("momentum_6m", "动量因子", 0.0, 0.0, "positive")

    prices = np.array(fund_data.close_history)
    returns = np.diff(prices) / prices[:-1]

    if len(returns) < window:
        return FactorScoreResult("momentum_6m", "动量因子", 0.0, 0.0, "positive")

    recent_returns = returns[-window:]
    # 2026-08-26 修复 off-by-one：prices[-window] 只有 window-1 个交易日区间，
    # 与分母 returns[-window:]（window 个区间）不一致；改用 prices[-window-1]
    total_return = prices[-1] / prices[-window - 1] - 1
    vol = float(np.std(recent_returns))
    momentum = total_return / (vol * np.sqrt(window)) if vol > 0 else 0.0

    rules = [
        {"condition": "> 1.0", "score": 1.0},
        {"condition": "> 0.5", "score": 0.5},
        {"condition": ">= -0.5 and <= 0.5", "score": 0.0},
        {"condition": ">= -1.0 and < -0.5", "score": -0.5},
        {"condition": "< -1.0", "score": -1.0},
    ]
    score = evaluate_signal_rules(momentum, rules)
    return FactorScoreResult("momentum_6m", "动量因子", round(momentum, 4), score, "positive")


def calculate_inv_volatility(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """波动率倒数 — 正向（低波动加分）, Z-score 标准化

    公式: 1 / (std(returns, 60) * sqrt(252))
    将日波动率年化后取倒数，低波动 → 高分
    返回值作为 pre-norm score，后续做截面 Z-score
    """
    window = (params or {}).get("window", 60)

    if not fund_data.close_history or len(fund_data.close_history) < window + 5:
        logger.warning(f"波动率倒数数据不足 code={fund_data.code}")
        return FactorScoreResult("inv_volatility", "波动率倒数", 0.0, 0.0, "positive")

    prices = np.array(fund_data.close_history)
    returns = np.diff(prices) / prices[:-1]
    recent = returns[-window:]
    vol = float(np.std(recent))
    inv_vol = 1.0 / (vol * np.sqrt(252)) if vol > 0 else 0.0

    # 返回 raw 的 inv_vol 作为评分（标准化阶段会做 Z-score 映射）
    return FactorScoreResult("inv_volatility", "波动率倒数", round(inv_vol, 6), round(inv_vol, 6), "positive")


def calculate_info_ratio(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """信息比率 — 正向（超额收益 / 跟踪误差）

    公式:
      excess_returns = fund_returns - benchmark_returns
      annualized_excess = mean(excess_returns) * 252
      tracking_error = std(excess_returns) * sqrt(252)
      IR = annualized_excess / tracking_error
    返回值作为 pre-norm score，后续做截面 Z-score
    """
    window = (params or {}).get("window", 252)

    if (not fund_data.close_history or not fund_data.benchmark_history
            or len(fund_data.close_history) < window + 10
            or len(fund_data.benchmark_history) < window + 10):
        logger.warning(f"信息比率数据不足 code={fund_data.code}")
        return FactorScoreResult("info_ratio", "信息比率", 0.0, 0.0, "positive")

    fund_prices, bench_prices = align_price_series(fund_data)

    fund_returns = np.diff(fund_prices) / fund_prices[:-1]
    bench_returns = np.diff(bench_prices) / bench_prices[:-1]

    excess = fund_returns - bench_returns
    recent_excess = excess[-window:]

    annualized_excess = float(np.mean(recent_excess)) * 252
    tracking_error = float(np.std(recent_excess)) * np.sqrt(252)

    ir = annualized_excess / tracking_error if tracking_error > 0 else 0.0

    return FactorScoreResult("info_ratio", "信息比率", round(ir, 4), round(ir, 4), "positive")


def calculate_macd_signal(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """MACD 信号 — 正向

    公式: DIF=EMA(12)-EMA(26), DEA=EMA(DIF,9), MACD柱=2*(DIF-DEA)
    信号: 金叉+放量→1.0, 金叉+缩量→0.5, 死叉+放量→-1.0, else→0
    """
    p = params or {}
    fast = p.get("fast", 12)
    slow = p.get("slow", 26)
    signal = p.get("signal", 9)

    if not fund_data.close_history or len(fund_data.close_history) < slow + signal + 5:
        logger.warning(f"MACD数据不足 code={fund_data.code}")
        return FactorScoreResult("macd_signal", "MACD信号", 0.0, 0.0, "positive")

    closes = np.array(fund_data.close_history)
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    dif = ema_fast - ema_slow
    dea = ema(dif, signal)
    macd_hist = 2 * (dif - dea)

    current_dif = float(dif[-1])
    current_dea = float(dea[-1])
    current_hist = float(macd_hist[-1])
    prev_hist = float(macd_hist[-2]) if len(macd_hist) > 1 else current_hist
    hist_delta = current_hist - prev_hist

    if current_dif > current_dea and hist_delta > 0:
        score = 1.0
    elif current_dif > current_dea and hist_delta <= 0:
        score = 0.5
    elif current_dif < current_dea and hist_delta < 0:
        score = -1.0
    else:
        score = 0.0

    return FactorScoreResult("macd_signal", "MACD信号", round(current_hist, 4), score, "positive")


def calculate_max_drawdown(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """最大回撤 — 正向（回撤小得分高）

    公式: max_drawdown = max(1 - price / rolling_max_price) over window
    返回值作为 pre-norm score（取负号：回撤越小值越高），后续做截面 Z-score
    """
    window = (params or {}).get("window", 252)

    if not fund_data.close_history or len(fund_data.close_history) < window + 5:
        logger.warning(f"最大回撤数据不足 code={fund_data.code}")
        return FactorScoreResult("max_drawdown", "最大回撤", 0.0, 0.0, "positive")

    prices = np.array(fund_data.close_history[-window:])
    rolling_max = np.maximum.accumulate(prices)
    drawdowns = 1 - prices / rolling_max
    mdd = float(np.max(drawdowns))

    # 取负值：回撤越小 → 值越大（正分）
    pre_norm = -mdd

    return FactorScoreResult("max_drawdown", "最大回撤", round(mdd, 4), round(pre_norm, 4), "positive")


def calculate_size_stability(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """规模稳定性 — 正向

    公式:
      size_cv = std(4季度规模) / mean(4季度规模)
      stability = 1 / size_cv
      附加调整：2亿~50亿 +0.2，超过100亿 -0.1
      final = stability + bonus
    返回值作为 pre-norm score，后续做截面 Z-score
    """
    window = (params or {}).get("window", 4)

    if not fund_data.fund_size_history or len(fund_data.fund_size_history) < window:
        logger.warning(f"规模稳定性数据不足 code={fund_data.code}")
        return FactorScoreResult("size_stability", "规模稳定性", 0.0, 0.0, "positive")

    sizes = np.array(fund_data.fund_size_history[-window:], dtype=float)
    mean_size = float(np.mean(sizes))
    std_size = float(np.std(sizes))

    if mean_size <= 0 or std_size <= 0:
        return FactorScoreResult("size_stability", "规模稳定性", 0.0, 0.0, "positive")

    size_cv = std_size / mean_size
    # cap=5：规模几乎不变时 1/CV 无界（CV=0.1% → stability=1000），
    # 若该因子未配置截面标准化，原始值直接进加权求和会打爆总分
    stability = min(1.0 / size_cv, 5.0)

    # 规模调整因子（当前最新规模）
    latest_size = sizes[-1]
    bonus = 0.0
    if 2e8 <= latest_size <= 5e9:
        bonus = 0.2
    elif latest_size > 1e10:
        bonus = -0.1

    final = min(stability, 5.0) + bonus

    return FactorScoreResult("size_stability", "规模稳定性", round(final, 4), round(final, 4), "positive")


# ═══════════════════════════════════════════════════════════════════════
# 7 因子计算函数（用户自定义系统）
# 标准化方式: Z-score 因子返回 raw_value，无标准化因子内嵌信号规则
# ═══════════════════════════════════════════════════════════════════════


def calculate_short_momentum(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """短期动量（20 日）— 正向, Z-score 标准化

    公式: nav / shift(nav, 20) - 1
    窗口: 20 日
    信号(标准化后): >0.01 买入, <-0.01 卖出, 其余观望
    """
    window = (params or {}).get("window", 20)
    if not fund_data.close_history or len(fund_data.close_history) < window + 2:
        return FactorScoreResult("short_momentum", "短期动量", 0.0, 0.0, "positive")

    prices = np.array(fund_data.close_history)
    mom = prices[-1] / prices[-window - 1] - 1

    # Z-score 因子返回 raw_value 作为 score，供截面标准化使用
    return FactorScoreResult("short_momentum", "短期动量", round(mom, 6), round(mom, 6), "positive")


def calculate_mid_momentum(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """中期动量（60 日）— 正向, Z-score 标准化

    公式: nav / shift(nav, 60) - 1
    窗口: 60 日
    信号(标准化后): >0 买入, <0 卖出, 其余观望
    """
    window = (params or {}).get("window", 60)
    if not fund_data.close_history or len(fund_data.close_history) < window + 2:
        return FactorScoreResult("mid_momentum", "中期动量", 0.0, 0.0, "positive")

    prices = np.array(fund_data.close_history)
    mom = prices[-1] / prices[-window - 1] - 1

    return FactorScoreResult("mid_momentum", "中期动量", round(mom, 6), round(mom, 6), "positive")


def calculate_drawdown_recovery(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """回撤修复度 — 正向, 无标准化（内嵌信号规则）

    公式: nav / rolling_max(nav, 252)
    当前净值占 252 日最高净值的比例，越接近 1 回撤修复越好。
    信号: >0.95 → 1.0 (买入), 0.85~0.95 → 0.0 (观望), <0.85 → -1.0 (卖出)
    """
    window = (params or {}).get("window", 252)
    if not fund_data.close_history or len(fund_data.close_history) < 60:
        return FactorScoreResult("drawdown_recovery", "回撤修复度", 0.0, 0.0, "positive")

    prices = np.array(fund_data.close_history[-window:])
    rolling_max = float(np.maximum.accumulate(prices)[-1])
    current = float(prices[-1])

    if rolling_max <= 0:
        return FactorScoreResult("drawdown_recovery", "回撤修复度", 1.0, 1.0, "positive")

    ratio = current / rolling_max

    # 内嵌信号规则（无标准化）
    rules = [
        {"condition": "> 0.95", "score": 1.0},
        {"condition": ">= 0.85", "score": 0.0},
        {"condition": "< 0.85", "score": -1.0},
    ]
    score = evaluate_signal_rules(ratio, rules)
    return FactorScoreResult("drawdown_recovery", "回撤修复度", round(ratio, 4), score, "positive")


def calculate_return_risk_ratio(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """收益风险比 — 正向, Z-score 标准化

    公式: mean(returns, 60) / (std(returns, 60) + 0.0001)
    加极小值 0.0001 防除零。正值表示正期望收益。
    信号(标准化后): >0.5σ 买入, <-0.5σ 卖出, 其余观望
    """
    window = (params or {}).get("window", 60)
    epsilon = (params or {}).get("epsilon", 0.0001)
    if not fund_data.close_history or len(fund_data.close_history) < window + 2:
        return FactorScoreResult("return_risk_ratio", "收益风险比", 0.0, 0.0, "positive")

    prices = np.array(fund_data.close_history[-window - 1:])
    returns = np.diff(prices) / prices[:-1]

    if len(returns) < 2:
        return FactorScoreResult("return_risk_ratio", "收益风险比", 0.0, 0.0, "positive")

    ratio = float(np.mean(returns)) / (float(np.std(returns)) + epsilon)
    return FactorScoreResult("return_risk_ratio", "收益风险比", round(ratio, 6), round(ratio, 6), "positive")


def calculate_momentum_accel(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """动量加速度 — 正向, Z-score 标准化

    公式: mom20 - mom60
    窗口: 60 日（短期 20 日, 中期 60 日）
    正值表示短期动量强于中期（加速上涨），负值表示减速。
    信号(标准化后): >0 加速买入, <0 减速卖出, ≈0 观望
    """
    short_w = (params or {}).get("short_window", 20)
    mid_w = (params or {}).get("mid_window", 60)
    lookback = max(short_w, mid_w)

    if not fund_data.close_history or len(fund_data.close_history) < lookback + 2:
        return FactorScoreResult("momentum_accel", "动量加速度", 0.0, 0.0, "positive")

    prices = np.array(fund_data.close_history)
    mom20 = prices[-1] / prices[-short_w - 1] - 1
    mom60 = prices[-1] / prices[-mid_w - 1] - 1
    accel = mom20 - mom60

    return FactorScoreResult("momentum_accel", "动量加速度", round(accel, 6), round(accel, 6), "positive")


def calculate_trend_consistency(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """趋势一致性 — 正向, Z-score 标准化

    公式: mean([sign(mom20), sign(mom60)])
    计算 20 日和 60 日动量方向的符号平均值：
      +1 → 两周期同向上涨，趋势强
       0 → 一正一负，趋势分歧
      -1 → 两周期同向下跌，趋势弱
    信号(标准化后): 同向买入, 反向卖出, 其余观望
    """
    short_w = (params or {}).get("short_window", 20)
    mid_w = (params or {}).get("mid_window", 60)
    lookback = max(short_w, mid_w)

    if not fund_data.close_history or len(fund_data.close_history) < lookback + 2:
        return FactorScoreResult("trend_consistency", "趋势一致性", 0.0, 0.0, "positive")

    prices = np.array(fund_data.close_history)
    mom20 = prices[-1] / prices[-short_w - 1] - 1
    mom60 = prices[-1] / prices[-mid_w - 1] - 1

    sign20 = 1.0 if mom20 > 0 else -1.0 if mom20 < 0 else 0.0
    sign60 = 1.0 if mom60 > 0 else -1.0 if mom60 < 0 else 0.0
    consistency = (sign20 + sign60) / 2.0

    return FactorScoreResult("trend_consistency", "趋势一致性", round(consistency, 4), round(consistency, 4), "positive")


# ═══════════════════════════════════════════════════════════════════════
# 市场环境因子（模块级快照上下文）
# ═══════════════════════════════════════════════════════════════════════

# 当前市场环境快照（MarketRegimeSnapshot 或 None）。
# 市场环境因子不依赖单只基金的 fund_data，而是读取此模块级上下文；
# 由 analysis_service 在每轮分析前注入，快照缺失时因子返回中性 0 分。
_current_regime: Optional[object] = None


def set_current_regime(snapshot: Optional[object]) -> None:
    """注入当前市场环境快照（None 表示无数据，因子将降级为中性分）"""
    global _current_regime
    _current_regime = snapshot


def get_current_regime() -> Optional[object]:
    """读取当前市场环境快照"""
    return _current_regime


def _regime_field(name: str, params: Optional[dict] = None) -> Optional[float]:
    """安全读取快照字段（快照缺失/字段为 None 时返回 None）

    优先读 params["_regime_snapshot"]（分析任务注入，任务间隔离、无竞态；
    键存在即权威——即使值为 None 也不回退全局，避免旧任务快照污染），
    键不存在时回退模块级全局（向后兼容旧调用方）。
    """
    if params is not None and "_regime_snapshot" in params:
        regime = params["_regime_snapshot"]
    else:
        regime = _current_regime
    if regime is None:
        return None
    return getattr(regime, name, None)


def calculate_market_valuation(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """大盘估值分位 — 负向（低分位=便宜=高分）

    原始值: 沪深300 PE 近5年分位 (0~1)，来自 MarketRegimeService。
    信号: ≤0.2→1.0, ≤0.4→0.5, ≤0.6→0, ≤0.8→-0.5, >0.8→-1.0
    快照缺失时返回中性 0 分。
    """
    pct = _regime_field("valuation_percentile", params)
    if pct is None:
        return FactorScoreResult("market_valuation", "大盘估值分位", 0.0, 0.0, "negative")

    rules = [
        {"condition": "<= 0.2", "score": 1.0},
        {"condition": "<= 0.4", "score": 0.5},
        {"condition": "<= 0.6", "score": 0.0},
        {"condition": "<= 0.8", "score": -0.5},
        {"condition": "> 0.8", "score": -1.0},
    ]
    score = evaluate_signal_rules(pct, rules)
    return FactorScoreResult("market_valuation", "大盘估值分位", round(pct, 4), score, "negative")


def calculate_market_sentiment(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """市场情绪 — 正向

    原始值: 全市场涨跌家数比 (up-down)/(up+down)，-1~1。
    信号: >0.5→1.0, >0.2→0.5, ≥-0.2→0, ≥-0.5→-0.5, else→-1.0
    """
    ratio = _regime_field("adv_decline_ratio", params)
    if ratio is None:
        return FactorScoreResult("market_sentiment", "市场情绪", 0.0, 0.0, "positive")

    rules = [
        {"condition": "> 0.5", "score": 1.0},
        {"condition": "> 0.2", "score": 0.5},
        {"condition": ">= -0.2", "score": 0.0},
        {"condition": ">= -0.5", "score": -0.5},
        {"condition": "else", "score": -1.0},
    ]
    score = evaluate_signal_rules(ratio, rules)
    return FactorScoreResult("market_sentiment", "市场情绪", round(ratio, 4), score, "positive")


def calculate_market_fund_flow(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """资金面 — 正向（杠杆资金流入为正）

    原始值: 上交所融资融券余额 7 日变化率。
    信号: >0.03→1.0, >0.01→0.5, ≥-0.01→0, ≥-0.03→-0.5, else→-1.0
    """
    change = _regime_field("margin_change_pct_7d", params)
    if change is None:
        return FactorScoreResult("market_fund_flow", "资金面", 0.0, 0.0, "positive")

    rules = [
        {"condition": "> 0.03", "score": 1.0},
        {"condition": "> 0.01", "score": 0.5},
        {"condition": ">= -0.01", "score": 0.0},
        {"condition": ">= -0.03", "score": -0.5},
        {"condition": "else", "score": -1.0},
    ]
    score = evaluate_signal_rules(change, rules)
    return FactorScoreResult("market_fund_flow", "资金面", round(change, 6), score, "positive")


# ═══════════════════════════════════════════════════════════════════════
# 因子注册表
# ═══════════════════════════════════════════════════════════════════════

FACTOR_CALCULATORS: dict[str, Callable[[FundData, Optional[dict]], FactorScoreResult]] = {
    # 核心 8 因子（兼容）
    "price_percentile": calculate_price_percentile,
    "fed_model": calculate_fed_model,
    "momentum_6m": calculate_momentum_6m,
    "inv_volatility": calculate_inv_volatility,
    "info_ratio": calculate_info_ratio,
    "macd_signal": calculate_macd_signal,
    "max_drawdown": calculate_max_drawdown,
    "size_stability": calculate_size_stability,
    # 用户自定义 7 因子系统
    "short_momentum": calculate_short_momentum,
    "mid_momentum": calculate_mid_momentum,
    "drawdown_recovery": calculate_drawdown_recovery,
    "return_risk_ratio": calculate_return_risk_ratio,
    "momentum_accel": calculate_momentum_accel,
    "trend_consistency": calculate_trend_consistency,
    # 市场环境 3 因子（读模块级 regime 快照，所有基金同分）
    "market_valuation": calculate_market_valuation,
    "market_sentiment": calculate_market_sentiment,
    "market_fund_flow": calculate_market_fund_flow,
}


# ═══════════════════════════════════════════════════════════════════════
# 因子引擎主类
# ═══════════════════════════════════════════════════════════════════════

class FactorEngine:
    """因子计算引擎 — 注册 + 调度 + 计算 + 标准化"""

    def __init__(self) -> None:
        self._calculators = FACTOR_CALCULATORS.copy()

    def register(self, code: str, calculator: Callable[[FundData, Optional[dict]], FactorScoreResult]) -> None:
        """注册自定义因子计算函数"""
        self._calculators[code] = calculator

    def calculate_all(
        self,
        fund_data: FundData,
        factors: list[dict],
    ) -> list[FactorScoreResult]:
        """计算单只基金的所有因子评分

        Args:
            fund_data: 基金数据
            factors: 因子配置列表

        Returns:
            因子评分结果列表（score 范围 -1.0 ~ +1.0）
        """
        results: list[FactorScoreResult] = []

        for factor in factors:
            code = factor.get("code", "")
            name = factor.get("name", code)
            params_str = factor.get("params", "{}")
            # direction 仅作元数据展示，此处不做翻转：内置因子的信号规则已编码方向
            # （如 price_percentile 的规则本身就是"低位高分"），若在此按 direction
            # 翻转 score 会把已按负向设计的因子反向，产生错误信号
            direction = factor.get("direction", "positive")

            if isinstance(params_str, str):
                try:
                    params = json.loads(params_str) if params_str else {}
                except json.JSONDecodeError:
                    params = {}
            else:
                params = params_str or {}

            calculator = self._calculators.get(code)
            if calculator is None:
                logger.warning(f"因子 {code} 无注册计算函数，跳过")
                results.append(FactorScoreResult(
                    factor_code=code, factor_name=name,
                    raw_value=0.0, score=0.0, direction=direction,
                ))
                continue

            try:
                result = calculator(fund_data, params)
                # direction 指导评分方向（仅在正向/反向规则相反时翻转）
                # 注意: signal_rules 已经编码了方向，通常不再翻转
                results.append(result)
            except Exception as e:
                logger.error(f"因子 {code} 计算异常: {e}", exc_info=True)
                results.append(FactorScoreResult(
                    factor_code=code, factor_name=name,
                    raw_value=0.0, score=0.0, direction=direction,
                ))

        return results

    def normalize_cross_sectional(
        self,
        all_results: dict[str, list[FactorScoreResult]],
        factors: list[dict],
    ) -> dict[str, list[FactorScoreResult]]:
        """对所有基金的因子结果做截面标准化

        需要 cross_sectional_zscore 的因子，收集所有基金该因子的
        pre-norm score，做 Z-score 后重新映射为 -1~+1。

        Args:
            all_results: {fund_code: [FactorScoreResult, ...]}
            factors: 因子配置列表

        Returns:
            更新后的 all_results
        """
        # 找出需要截面标准化的因子索引和配置
        normalize_configs = {}
        for fi, factor in enumerate(factors):
            norm = factor.get("normalization", "none")
            if norm == "cross_sectional_zscore":
                norm_conf = factor.get("normalization_config") or {}
                thresholds = None
                if isinstance(norm_conf, dict):
                    thresholds = norm_conf.get("zscore_thresholds")
                normalize_configs[fi] = thresholds

        if not normalize_configs:
            return all_results

        # 对每个需要标准化的因子索引做跨基金 Z-score
        for fi, thresholds in normalize_configs.items():
            scores_map = {}
            for fund_code, results_list in all_results.items():
                if fi < len(results_list):
                    scores_map[fund_code] = results_list[fi].score

            if len(scores_map) < 2:
                # 单只基金：截面标准化不可行，赋中性值 0.0
                for fund_code in scores_map:
                    if fi < len(all_results[fund_code]):
                        all_results[fund_code][fi].score = 0.0
                logger.warning(
                    f"截面标准化: 基金池仅 1 只，因子索引 {fi} 全部取中性 0.0"
                    "（截面因子无信号，建议停用该因子或扩充基金池）"
                )
                continue

            if len(scores_map) < 5:
                # 小池退化：z-score 只反映池内相对排名。2 只基金时两值 z=±1
                #（+0.5/-1.0 不对称分档），即使所有基金 pre-norm 同为正，
                # 也必有一只拿满档负分——无绝对价值判断意义
                logger.warning(
                    f"截面标准化: 基金池仅 {len(scores_map)} 只 (<5)，"
                    f"因子索引 {fi} 得分为纯池内相对排名，可能失真；"
                    "建议扩充基金池或改用无标准化因子"
                )

            normalized = apply_cross_sectional_zscore(scores_map, thresholds)

            for fund_code, new_score in normalized.items():
                if fi < len(all_results[fund_code]):
                    all_results[fund_code][fi].score = new_score

            logger.info(f"截面标准化: 因子索引 {fi}, {len(normalized)} 只基金")

        return all_results


# 全局引擎实例
factor_engine = FactorEngine()
