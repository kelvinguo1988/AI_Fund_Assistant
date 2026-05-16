"""加权评分引擎 + 信号生成

评分规则：
- 加权总分 ≥ 3.5 → buy（买入信号）
- 加权总分 ≤ 2.0 → sell（卖出信号）
- 其余 → hold（持有观望）

买入分档：
- 3.5-4.0 → light_buy（轻仓）
- 4.0-4.5 → moderate_buy（中仓）
- ≥4.5 → heavy_buy（重仓）

卖出分档：
- ≤2.0 → light_sell（轻仓减）
- ≤1.5 → moderate_sell（中仓减）
- <1.0 → heavy_sell（清仓）
"""

import logging
from dataclasses import dataclass

from backend.engines.factor_engine import FactorScoreResult

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """信号判定结果"""
    weighted_score: float          # 加权总分 0-5
    signal_direction: str          # buy / sell / hold
    signal_strength: str           # light_buy / moderate_buy / heavy_buy / ...
    operation_advice: str          # 操作建议文本


class ScoringEngine:
    """加权评分引擎"""

    def compute(
        self,
        factor_scores: list[FactorScoreResult],
        factor_weights: list[float],
        buy_threshold: float = 3.5,
        sell_threshold: float = 2.0,
    ) -> SignalResult:
        """计算加权评分并生成信号

        Args:
            factor_scores: 因子评分结果列表
            factor_weights: 因子权重列表（与 factor_scores 一一对应）
            buy_threshold: 买入阈值，默认 3.5
            sell_threshold: 卖出阈值，默认 2.0

        Returns:
            SignalResult 信号判定结果
        """
        if not factor_scores or not factor_weights:
            logger.warning("因子评分或权重为空，返回中性信号")
            return SignalResult(
                weighted_score=2.5,
                signal_direction="hold",
                signal_strength="hold",
                operation_advice="数据不足，建议观望",
            )

        # 计算加权总分
        total_weight = sum(factor_weights)
        if total_weight <= 0:
            total_weight = 1.0

        weighted_sum = 0.0
        for score, weight in zip(factor_scores, factor_weights):
            weighted_sum += score.score * weight

        weighted_score = weighted_sum / total_weight
        weighted_score = round(max(0.0, min(5.0, weighted_score)), 2)

        # 判定信号方向和强度
        direction, strength = self._determine_signal(
            weighted_score, buy_threshold, sell_threshold
        )

        # 生成操作建议
        advice = self._generate_advice(direction, strength, weighted_score)

        return SignalResult(
            weighted_score=weighted_score,
            signal_direction=direction,
            signal_strength=strength,
            operation_advice=advice,
        )

    def _determine_signal(
        self,
        score: float,
        buy_threshold: float,
        sell_threshold: float,
    ) -> tuple[str, str]:
        """判定信号方向和强度

        Args:
            score: 加权评分
            buy_threshold: 买入阈值
            sell_threshold: 卖出阈值

        Returns:
            (direction, strength) 元组
        """
        if score >= buy_threshold:
            direction = "buy"
            if score >= 4.5:
                strength = "heavy_buy"
            elif score >= 4.0:
                strength = "moderate_buy"
            else:
                strength = "light_buy"
        elif score <= sell_threshold:
            direction = "sell"
            if score < 1.0:
                strength = "heavy_sell"
            elif score <= 1.5:
                strength = "moderate_sell"
            else:
                strength = "light_sell"
        else:
            direction = "hold"
            strength = "hold"

        return direction, strength

    def _generate_advice(
        self,
        direction: str,
        strength: str,
        score: float,
    ) -> str:
        """生成操作建议文本

        Args:
            direction: 信号方向
            strength: 信号强度
            score: 加权评分

        Returns:
            操作建议文本
        """
        advice_templates = {
            "heavy_buy": f"综合评分 {score}，强烈建议加仓，可考虑较大仓位配置",
            "moderate_buy": f"综合评分 {score}，建议适度加仓，控制仓位比例",
            "light_buy": f"综合评分 {score}，可小幅加仓或持有观察",
            "hold": f"综合评分 {score}，建议持有观望，暂不加仓或减仓",
            "light_sell": f"综合评分 {score}，可小幅减仓，关注后续走势",
            "moderate_sell": f"综合评分 {score}，建议适度减仓，降低风险暴露",
            "heavy_sell": f"综合评分 {score}，强烈建议减仓或清仓，规避风险",
        }

        return advice_templates.get(strength, f"综合评分 {score}，建议观望")


# 全局引擎实例
scoring_engine = ScoringEngine()
