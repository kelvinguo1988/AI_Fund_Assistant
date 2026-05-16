"""因子计算引擎 — 5 因子注册 + 计算调度

因子列表：
1. PE百分位 (pe_percentile)  — 权重 1.5, 正向
2. 股债性价比FED (fed_model)  — 权重 1.5, 正向
3. MACD信号 (macd_signal)    — 权重 1.0, 正向
4. 均线趋势 (ma_trend)       — 权重 1.0, 正向
5. 成交量变化 (volume_change) — 权重 1.0, 正向
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
    raw_value: float      # 原始值
    score: float          # 0-5 标准化评分
    direction: str        # positive / negative


# ── 评分工具函数 ─────────────────────────────────────────────────────

def percentile_to_score(percentile: float) -> float:
    """百分位 → 0-5 评分

    百分位越高，评分越高（正向因子用）。
    百分位 0% → 0 分，100% → 5 分

    Args:
        percentile: 0-100 的百分位值

    Returns:
        0-5 的评分
    """
    return max(0.0, min(5.0, percentile / 20.0))


def inverse_percentile_to_score(percentile: float) -> float:
    """百分位 → 反向 0-5 评分

    百分位越低，评分越高（反向因子用）。
    百分位 0% → 5 分，100% → 0 分

    Args:
        percentile: 0-100 的百分位值

    Returns:
        0-5 的评分
    """
    return max(0.0, min(5.0, (100.0 - percentile) / 20.0))


# ── 5 个因子计算函数 ─────────────────────────────────────────────────

def calculate_pe_percentile(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """PE 百分位因子

    计算当前 PE 在历史 PE 序列中的百分位，百分位越高表示估值越低（越值得买）。

    Args:
        fund_data: 基金数据
        params: 参数，含 period（回看年数，默认 5）

    Returns:
        FactorScoreResult
    """
    period_years = (params or {}).get("period", 5)

    if fund_data.pe is None or not fund_data.close_history:
        logger.warning(f"PE 百分位因子数据不足 code={fund_data.code}")
        return FactorScoreResult(
            factor_code="pe_percentile",
            factor_name="PE百分位",
            raw_value=0.0,
            score=2.5,  # 默认中性评分
            direction="positive",
        )

    # 如果有 close_history，用价格序列近似百分位
    # 真正的 PE 百分位需要 PE 历史数据，此处用价格百分位作为近似
    closes = np.array(fund_data.close_history)
    current = fund_data.pe  # 使用 PE 作为原始值

    # 计算 PE 在历史中的百分位
    percentile = float(np.sum(closes[-1] >= closes) / len(closes) * 100) if len(closes) > 0 else 50.0

    score = percentile_to_score(percentile)

    return FactorScoreResult(
        factor_code="pe_percentile",
        factor_name="PE百分位",
        raw_value=round(percentile, 2),
        score=round(score, 2),
        direction="positive",
    )


def calculate_fed_model(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """股债性价比 FED 模型因子

    FED = 1/PE - 债券收益率
    FED 越高，股票相对债券越有吸引力。

    Args:
        fund_data: 基金数据
        params: 无特殊参数

    Returns:
        FactorScoreResult
    """
    if fund_data.pe is None or fund_data.pe <= 0 or fund_data.bond_yield is None:
        logger.warning(f"FED 模型因子数据不足 code={fund_data.code}")
        return FactorScoreResult(
            factor_code="fed_model",
            factor_name="股债性价比FED",
            raw_value=0.0,
            score=2.5,
            direction="positive",
        )

    # 计算 FED 值
    earnings_yield = 1.0 / fund_data.pe * 100  # 盈利收益率（%）
    fed_value = earnings_yield - fund_data.bond_yield  # FED 差值

    # FED 值映射到 0-5 评分
    # 典型范围：-3% 到 +6%
    # FED > 3% → 非常有吸引力 → 4.5+
    # FED 0-3% → 中等吸引力 → 2.5-4.5
    # FED < 0% → 无吸引力 → 0-2.5
    if fed_value >= 3.0:
        score = 4.5 + min(0.5, (fed_value - 3.0) / 6.0)
    elif fed_value >= 0:
        score = 2.5 + (fed_value / 3.0) * 2.0
    else:
        score = max(0.0, 2.5 + (fed_value / 3.0) * 2.5)

    return FactorScoreResult(
        factor_code="fed_model",
        factor_name="股债性价比FED",
        raw_value=round(fed_value, 4),
        score=round(score, 2),
        direction="positive",
    )


def calculate_macd_signal(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """MACD 信号因子

    基于 MACD 金叉/死叉判断趋势方向。
    - MACD > 0 且 Signal > 0 → 强多头 → 4-5
    - MACD > 0 且 Signal < 0 → 弱多头 → 3-4
    - MACD < 0 且 Signal > 0 → 弱空头 → 2-3
    - MACD < 0 且 Signal < 0 → 强空头 → 0-2

    Args:
        fund_data: 基金数据
        params: 参数，含 fast(12), slow(26), signal(9)

    Returns:
        FactorScoreResult
    """
    p = params or {}
    fast_period = p.get("fast", 12)
    slow_period = p.get("slow", 26)
    signal_period = p.get("signal", 9)

    if not fund_data.close_history or len(fund_data.close_history) < slow_period + signal_period:
        logger.warning(f"MACD 信号因子数据不足 code={fund_data.code}")
        return FactorScoreResult(
            factor_code="macd_signal",
            factor_name="MACD信号",
            raw_value=0.0,
            score=2.5,
            direction="positive",
        )

    closes = np.array(fund_data.close_history)

    # 计算 EMA
    ema_fast = _calculate_ema(closes, fast_period)
    ema_slow = _calculate_ema(closes, slow_period)

    # DIF = 快线 - 慢线
    dif = ema_fast - ema_slow

    # DEA = DIF 的 EMA
    dea = _calculate_ema(dif, signal_period)

    # MACD 柱 = 2 * (DIF - DEA)
    macd_hist = 2 * (dif - dea)

    # 取最新值
    current_macd = float(macd_hist[-1]) if len(macd_hist) > 0 else 0.0
    current_dif = float(dif[-1]) if len(dif) > 0 else 0.0

    # 评分逻辑
    if current_dif > 0 and current_macd > 0:
        score = 4.0 + min(1.0, abs(current_macd) / (np.std(macd_hist) + 1e-8) * 0.3)
    elif current_dif > 0 and current_macd <= 0:
        score = 3.0 + min(1.0, abs(current_dif) / (np.std(dif) + 1e-8) * 0.3)
    elif current_dif <= 0 and current_macd > 0:
        score = 2.0 + min(1.0, abs(current_macd) / (np.std(macd_hist) + 1e-8) * 0.3)
    else:
        score = max(0.0, 2.0 - min(2.0, abs(current_macd) / (np.std(macd_hist) + 1e-8) * 0.3))

    return FactorScoreResult(
        factor_code="macd_signal",
        factor_name="MACD信号",
        raw_value=round(current_macd, 4),
        score=round(max(0.0, min(5.0, score)), 2),
        direction="positive",
    )


def calculate_ma_trend(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """均线趋势因子

    短期均线与长期均线的位置关系及趋势强度。
    - 价格 > MA_short > MA_long → 多头排列 → 4-5
    - MA_short > MA_long → 弱多头 → 3-4
    - MA_short < MA_long → 弱空头 → 2-3
    - 价格 < MA_short < MA_long → 空头排列 → 0-2

    Args:
        fund_data: 基金数据
        params: 参数，含 short_period(20), long_period(60)

    Returns:
        FactorScoreResult
    """
    p = params or {}
    short_period = p.get("short_period", 20)
    long_period = p.get("long_period", 60)

    if not fund_data.close_history or len(fund_data.close_history) < long_period + 5:
        logger.warning(f"均线趋势因子数据不足 code={fund_data.code}")
        return FactorScoreResult(
            factor_code="ma_trend",
            factor_name="均线趋势",
            raw_value=0.0,
            score=2.5,
            direction="positive",
        )

    closes = np.array(fund_data.close_history)

    ma_short = float(np.mean(closes[-short_period:]))
    ma_long = float(np.mean(closes[-long_period:]))
    current_price = float(closes[-1])

    # MA 趋势差值百分比
    ma_diff_pct = (ma_short - ma_long) / ma_long * 100 if ma_long > 0 else 0.0

    # 评分逻辑
    if current_price > ma_short > ma_long:
        # 多头排列
        score = 4.0 + min(1.0, abs(ma_diff_pct) / 2.0)
    elif ma_short > ma_long:
        # 弱多头
        score = 3.0 + min(1.0, abs(ma_diff_pct) / 2.0)
    elif current_price < ma_short < ma_long:
        # 空头排列
        score = max(0.0, 2.0 - min(2.0, abs(ma_diff_pct) / 2.0))
    else:
        # 弱空头
        score = max(0.0, 2.0 + ma_diff_pct / 2.0)

    return FactorScoreResult(
        factor_code="ma_trend",
        factor_name="均线趋势",
        raw_value=round(ma_diff_pct, 4),
        score=round(max(0.0, min(5.0, score)), 2),
        direction="positive",
    )


def calculate_volume_change(fund_data: FundData, params: Optional[dict] = None) -> FactorScoreResult:
    """成交量变化因子

    近期成交量相对历史均量的变化率。
    - 放量上涨 → 看多信号 → 3.5-5
    - 温和放量 → 中性偏多 → 2.5-3.5
    - 缩量 → 中性偏空 → 1.5-2.5
    - 放量下跌 → 看空信号 → 0-1.5

    Args:
        fund_data: 基金数据
        params: 参数，含 period(20)

    Returns:
        FactorScoreResult
    """
    period = (params or {}).get("period", 20)

    if not fund_data.volume_history or len(fund_data.volume_history) < period + 5:
        logger.warning(f"成交量变化因子数据不足 code={fund_data.code}")
        return FactorScoreResult(
            factor_code="volume_change",
            factor_name="成交量变化",
            raw_value=0.0,
            score=2.5,
            direction="positive",
        )

    volumes = np.array(fund_data.volume_history)
    closes = np.array(fund_data.close_history)

    # 近 period 天平均成交量
    avg_volume = float(np.mean(volumes[-period:]))
    # 最新成交量
    current_volume = float(volumes[-1])

    # 成交量比率
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

    # 价格变化方向
    price_change = 0.0
    if len(closes) >= 2:
        price_change = (float(closes[-1]) - float(closes[-2])) / float(closes[-2]) * 100

    # 评分逻辑：结合量比和价格方向
    if price_change > 0:
        # 上涨 + 放量 → 强多
        if volume_ratio >= 1.5:
            score = 4.0 + min(1.0, (volume_ratio - 1.5) / 2.0)
        elif volume_ratio >= 1.0:
            score = 3.0 + (volume_ratio - 1.0) * 2.0
        else:
            # 上涨但缩量 → 偏弱
            score = 2.5 + (volume_ratio - 0.5)
    else:
        # 下跌
        if volume_ratio >= 1.5:
            # 放量下跌 → 偏空
            score = max(0.0, 1.5 - (volume_ratio - 1.5) * 0.5)
        elif volume_ratio >= 1.0:
            score = 2.0 - (volume_ratio - 1.0) * 0.5
        else:
            # 缩量下跌 → 可能见底
            score = 2.5

    return FactorScoreResult(
        factor_code="volume_change",
        factor_name="成交量变化",
        raw_value=round(volume_ratio, 4),
        score=round(max(0.0, min(5.0, score)), 2),
        direction="positive",
    )


# ── EMA 计算辅助函数 ─────────────────────────────────────────────────

def _calculate_ema(data: np.ndarray, period: int) -> np.ndarray:
    """计算指数移动平均线

    Args:
        data: 输入序列
        period: EMA 周期

    Returns:
        EMA 序列（与输入等长）
    """
    if len(data) < period:
        return np.array([float(np.mean(data))] * len(data))

    multiplier = 2.0 / (period + 1)
    ema = np.zeros_like(data, dtype=float)

    # 初始值用 SMA
    ema[period - 1] = float(np.mean(data[:period]))

    for i in range(period, len(data)):
        ema[i] = (data[i] - ema[i - 1]) * multiplier + ema[i - 1]

    # 前 period-1 个值用第一个有效值填充
    ema[:period - 1] = ema[period - 1]

    return ema


# ── 因子注册表 ───────────────────────────────────────────────────────

FACTOR_CALCULATORS: dict[str, Callable[[FundData, Optional[dict]], FactorScoreResult]] = {
    "pe_percentile": calculate_pe_percentile,
    "fed_model": calculate_fed_model,
    "macd_signal": calculate_macd_signal,
    "ma_trend": calculate_ma_trend,
    "volume_change": calculate_volume_change,
}


# ── 因子引擎主类 ─────────────────────────────────────────────────────

class FactorEngine:
    """因子计算引擎 — 注册 + 调度 + 计算"""

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
        """计算所有启用的因子评分

        Args:
            fund_data: 基金数据
            factors: 因子配置列表，每个元素含 code, name, params, direction 等

        Returns:
            因子评分结果列表
        """
        results: list[FactorScoreResult] = []

        for factor in factors:
            code = factor.get("code", "")
            name = factor.get("name", code)
            params_str = factor.get("params", "{}")
            direction = factor.get("direction", "positive")

            # 解析 params
            if isinstance(params_str, str):
                try:
                    params = json.loads(params_str) if params_str else {}
                except json.JSONDecodeError:
                    params = {}
            else:
                params = params_str if params_str else {}

            calculator = self._calculators.get(code)
            if calculator is None:
                logger.warning(f"因子 {code} 无注册计算函数，跳过")
                results.append(FactorScoreResult(
                    factor_code=code,
                    factor_name=name,
                    raw_value=0.0,
                    score=2.5,
                    direction=direction,
                ))
                continue

            try:
                result = calculator(fund_data, params)
                # 如果因子方向是反向，翻转评分
                if direction == "negative":
                    result.score = round(5.0 - result.score, 2)
                    result.direction = "negative"
                results.append(result)
            except Exception as e:
                logger.error(f"因子 {code} 计算异常: {e}")
                results.append(FactorScoreResult(
                    factor_code=code,
                    factor_name=name,
                    raw_value=0.0,
                    score=2.5,
                    direction=direction,
                ))

        return results


# 全局引擎实例
factor_engine = FactorEngine()
