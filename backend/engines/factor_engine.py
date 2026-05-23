"""因子计算引擎 — 8 因子 + 信号规则 + 截面标准化

因子列表（来自 README_1.md 配置体系）：
1. PE百分位 (pe_percentile)      — 负向, 权重 1.2
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


def rolling_mean(data: np.ndarray, period: int) -> np.ndarray:
    """滚动均值"""
    if len(data) < period or period <= 0:
        return np.array([float(np.mean(data))] * len(data)) if len(data) > 0 else data
    result = np.zeros_like(data, dtype=float)
    cumsum = np.cumsum(data)
    result[period - 1] = cumsum[period - 1] / period
    for i in range(period, len(data)):
        result[i] = (cumsum[i] - cumsum[i - period]) / period
    result[:period - 1] = result[period - 1]
    return result


def rolling_std(data: np.ndarray, period: int) -> np.ndarray:
    """滚动标准差"""
    if len(data) < period:
        return np.array([float(np.std(data))] * len(data)) if len(data) > 0 else data
    result = np.zeros_like(data, dtype=float)
    for i in range(period - 1, len(data)):
        result[i] = float(np.std(data[i - period + 1:i + 1]))
    result[:period - 1] = result[period - 1]
    return result


def shift(data: np.ndarray, n: int) -> np.ndarray:
    """向前移位，前 n 个元素用第一个值填充"""
    result = np.zeros_like(data)
    if len(data) <= n:
        result[:] = data[0]
        return result
    result[n:] = data[:-n]
    result[:n] = data[0]
    return result


def percentile_rank(value: float, history: np.ndarray) -> float:
    """计算 value 在 history 中的百分位排名 (0~1)"""
    if len(history) == 0:
        return 0.5
    return float(np.sum(history <= value)) / len(history)


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

def calculate_pe_percentile(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """PE 百分位 — 负向（低估值得分高）

    用当前价格对比历史价格序列，近似判断估值高低：
    价格处于历史低位 → 大概率低估 → 高分。
    公式: percentile_rank(close, close_history)
    信号: ≤0.2→1.0, ≤0.4→0.5, ≤0.6→0, ≤0.8→-0.5, >0.8→-1.0
    """
    window = (params or {}).get("window", 1250)

    current_close = fund_data.close or fund_data.pe
    if current_close is None:
        logger.warning(f"PE百分位数据不足 code={fund_data.code}")
        return FactorScoreResult("pe_percentile", "PE百分位", 0.0, 0.0, "negative")

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
    return FactorScoreResult("pe_percentile", "PE百分位", round(pct, 4), score, "negative")


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
    bond = fund_data.bond_yield if fund_data.bond_yield is not None else 2.5
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
    total_return = prices[-1] / prices[-window] - 1 if window < len(prices) else 0.0
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

    fund_prices = np.array(fund_data.close_history)
    bench_prices = np.array(fund_data.benchmark_history)

    fund_returns = np.diff(fund_prices) / fund_prices[:-1]
    bench_returns = np.diff(bench_prices) / bench_prices[:-1]

    # 对齐长度
    min_len = min(len(fund_returns), len(bench_returns))
    fund_returns = fund_returns[-min_len:]
    bench_returns = bench_returns[-min_len:]

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
    stability = 1.0 / size_cv

    # 规模调整因子（当前最新规模）
    latest_size = sizes[-1]
    bonus = 0.0
    if 2e8 <= latest_size <= 5e9:
        bonus = 0.2
    elif latest_size > 1e10:
        bonus = -0.1

    final = stability + bonus

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
    mom = prices[-1] / prices[-window] - 1

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
    mom = prices[-1] / prices[-window] - 1

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
    mom20 = prices[-1] / prices[-short_w] - 1
    mom60 = prices[-1] / prices[-mid_w] - 1
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
    mom20 = prices[-1] / prices[-short_w] - 1
    mom60 = prices[-1] / prices[-mid_w] - 1

    sign20 = 1.0 if mom20 > 0 else -1.0 if mom20 < 0 else 0.0
    sign60 = 1.0 if mom60 > 0 else -1.0 if mom60 < 0 else 0.0
    consistency = (sign20 + sign60) / 2.0

    return FactorScoreResult("trend_consistency", "趋势一致性", round(consistency, 4), round(consistency, 4), "positive")


# ═══════════════════════════════════════════════════════════════════════
# 因子注册表
# ═══════════════════════════════════════════════════════════════════════

FACTOR_CALCULATORS: dict[str, Callable[[FundData, Optional[dict]], FactorScoreResult]] = {
    # 核心 8 因子（兼容）
    "pe_percentile": calculate_pe_percentile,
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
                logger.info(f"截面标准化: 单只基金，因子索引 {fi} 使用中性值 0.0")
                continue

            normalized = apply_cross_sectional_zscore(scores_map, thresholds)

            for fund_code, new_score in normalized.items():
                if fi < len(all_results[fund_code]):
                    all_results[fund_code][fi].score = new_score

            logger.info(f"截面标准化: 因子索引 {fi}, {len(normalized)} 只基金")

        return all_results


# 全局引擎实例
factor_engine = FactorEngine()
