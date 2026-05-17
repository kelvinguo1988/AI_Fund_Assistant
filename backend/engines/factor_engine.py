"""因子计算引擎 — 7 因子 + 信号规则 + 截面标准化

因子列表（来自 README_1.md 配置体系）：
1. PE百分位 (pe_percentile)    — 负向, 权重 1.2
2. 股债性价比FED (fed_model)    — 正向, 权重 1.2
3. 动量因子 (momentum_6m)      — 正向, 权重 1.0
4. 波动率倒数 (inv_volatility)  — 正向, 权重 0.8
5. ROE稳定性 (roe_stability)    — 正向, 权重 0.8
6. MACD信号 (macd_signal)      — 正向, 权重 0.6
7. 量价配合 (volume_price)      — 正向, 权重 0.4

分值范围: -1.0 ~ +1.0（每因子），加权求和 → -6.0 ~ +6.0
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
    """截面 Z-score 标准化 → -1~+1 映射

    Args:
        scores: {fund_code: pre_norm_score}
        thresholds: [upper, middle, lower] 默认 [1.0, 0, -1.0]

    Returns:
        {fund_code: normalized_score}
    """
    values = np.array(list(scores.values()))
    if len(values) < 2 or np.std(values) == 0:
        return {k: 0.0 for k in scores}

    mean = float(np.mean(values))
    std = float(np.std(values))
    t = thresholds or [1.0, 0, -1.0]

    return {
        code: 1.0 if z > t[0] else 0.5 if z > t[1] else -0.5 if z >= t[2] else -1.0
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
    """波动率倒数 — 正向（低波动加分）

    公式: 1 / std(returns, 60)
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
    inv_vol = 1.0 / vol if vol > 0 else 0.0

    # 返回 raw 的 inv_vol 作为评分（标准化阶段会做 Z-score 映射）
    return FactorScoreResult("inv_volatility", "波动率倒数", round(inv_vol, 4), round(inv_vol, 4), "positive")


def calculate_roe_stability(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """ROE 稳定性 — 正向

    公式: mean(roe,4) / std(roe,4)
    依赖季报 ROE 数据，数据不可用时返回中性。
    """
    # FundData 无 ROE 历史，返回中性
    return FactorScoreResult("roe_stability", "ROE稳定性", 0.0, 0.0, "positive")


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


def calculate_volume_price(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """量价配合 — 正向

    公式: ((close>shift(close,5))*2-1) * (volume/mean(volume,5)-1)
    信号: >0.5→1.0, >0→0.5, ≤0→-0.5, <-0.5→-1.0
    """
    window = (params or {}).get("window", 5)

    if (not fund_data.close_history or not fund_data.volume_history
            or len(fund_data.close_history) < window + 2
            or len(fund_data.volume_history) < window + 2):
        logger.warning(f"量价配合数据不足 code={fund_data.code}")
        return FactorScoreResult("volume_price", "量价配合", 0.0, 0.0, "positive")

    closes = np.array(fund_data.close_history)
    volumes = np.array(fund_data.volume_history)

    price_up = float(closes[-1] > closes[-window - 1])
    vol_mean = float(np.mean(volumes[-window:]))
    vol_change = float(volumes[-1]) / vol_mean - 1 if vol_mean > 0 else 0.0

    raw = (price_up * 2 - 1) * vol_change

    rules = [
        {"condition": "> 0.5", "score": 1.0},
        {"condition": "> 0", "score": 0.5},
        {"condition": ">= -0.5 and <= 0", "score": -0.5},
        {"condition": "< -0.5", "score": -1.0},
    ]
    score = evaluate_signal_rules(raw, rules)
    return FactorScoreResult("volume_price", "量价配合", round(raw, 4), score, "positive")


# ═══════════════════════════════════════════════════════════════════════
# 因子注册表
# ═══════════════════════════════════════════════════════════════════════

FACTOR_CALCULATORS: dict[str, Callable[[FundData, Optional[dict]], FactorScoreResult]] = {
    "pe_percentile": calculate_pe_percentile,
    "fed_model": calculate_fed_model,
    "momentum_6m": calculate_momentum_6m,
    "inv_volatility": calculate_inv_volatility,
    "roe_stability": calculate_roe_stability,
    "macd_signal": calculate_macd_signal,
    "volume_price": calculate_volume_price,
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
                continue

            normalized = apply_cross_sectional_zscore(scores_map, thresholds)

            for fund_code, new_score in normalized.items():
                if fi < len(all_results[fund_code]):
                    all_results[fund_code][fi].score = new_score

            logger.info(f"截面标准化: 因子索引 {fi}, {len(normalized)} 只基金")

        return all_results


# 全局引擎实例
factor_engine = FactorEngine()
