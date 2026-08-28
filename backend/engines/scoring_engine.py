"""加权评分引擎 + 信号生成

评分规则（-6.4 ~ +6.4 五档对称体系）：
- 因子分值范围: -1.0 ~ +1.0 / 因子
- 加权求和: Σ(score × weight)，总权重 ≈ 6.4 → 总分范围 ≈ -6.4 ~ +6.4
- 阈值可从 system_config 表动态加载，通过 /api/system/scoring-config 前端可调。

变更记录:
- 2025-05: 因子分值从 0-5 改为 -1~+1，加权求和替代归一化
- 2026-05: 8 因子体系总权重 6.4，钳位范围对应调整
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.engines.factor_engine import FactorScoreResult

logger = logging.getLogger(__name__)

# ── 默认五档阈值配置（因子总分理论范围 -6.4 ~ +6.4）────────────────────
# min_score 降序排列，末档设 -6.4 作为兜底（等于理论最小值）
#
# 适用范围说明（2026-08-22 审计修复）：
# 五档阈值仅用于 ScoringEngine.compute 直接调用路径（无质量过滤的旧路径）。
# 走质量过滤的正式路径（compute_with_quality_filter）由 determine_signal 动态阈值
# 决策（base_buy_threshold/base_sell_threshold + 规模冲击/漂移上调），五档在此
# 路径不生效。前端调"五档阈值"实际只影响旧路径，请勿误判已调参。
DEFAULT_THRESHOLDS: list[dict] = [
    {
        "min_score": 3.0,
        "label": "强烈加仓",
        "signal_direction": "buy",
        "signal_strength": "heavy_buy",
        "operation_advice": "综合评分 {score}，强烈建议加仓，权益仓位可升至 {equity_pct}%",
        "equity_ratio": 0.9,
    },
    {
        "min_score": 1.5,
        "label": "适度加仓",
        "signal_direction": "buy",
        "signal_strength": "moderate_buy",
        "operation_advice": "综合评分 {score}，建议适度加仓，权益仓位可升至 {equity_pct}%",
        "equity_ratio": 0.7,
    },
    {
        "min_score": -1.5,
        "label": "中性/观望",
        "signal_direction": "hold",
        "signal_strength": "hold",
        "operation_advice": "综合评分 {score}，建议持有观望，维持基准仓位 {equity_pct}%",
        "equity_ratio": 0.5,
    },
    {
        "min_score": -3.0,
        "label": "适度减仓",
        "signal_direction": "sell",
        "signal_strength": "moderate_sell",
        "operation_advice": "综合评分 {score}，建议适度减仓，权益仓位降至 {equity_pct}%",
        "equity_ratio": 0.3,
    },
    {
        "min_score": -6.4,
        "label": "强烈减仓",
        "signal_direction": "sell",
        "signal_strength": "heavy_sell",
        "operation_advice": "综合评分 {score}，强烈建议减仓或清仓，权益仓位降至 {equity_pct}%",
        "equity_ratio": 0.1,
    },
]


@dataclass
class SignalResult:
    """信号判定结果"""
    weighted_score: float          # 归一化总分 -6.0 ~ +6.0
    raw_score: float               # 原始加权求和值（内部使用）
    signal_direction: str          # buy / sell / hold
    signal_strength: str           # heavy_buy / moderate_buy / hold / moderate_sell / heavy_sell
    operation_advice: str          # 操作建议文本
    equity_ratio: float            # 建议权益仓位比例 0.0-1.0
    # ── 第零层扩展字段（可选，向后兼容）──
    original_score: float = 0.0           # 因子修正前的原始评分
    dynamic_buy_threshold: float = 1.5    # 动态买入阈值
    quality_warnings: list[str] = field(default_factory=list)  # 质量过滤警告


class ScoringEngine:
    """加权评分引擎"""

    def _load_thresholds(self, config_json: Optional[str] = None) -> list[dict]:
        """从 JSON 字符串加载阈值配置，失败时使用默认值"""
        if not config_json:
            return DEFAULT_THRESHOLDS
        try:
            data = json.loads(config_json)
            if isinstance(data, list) and len(data) >= 3:
                return data
            return DEFAULT_THRESHOLDS
        except (json.JSONDecodeError, TypeError):
            logger.warning("评分阈值配置解析失败，使用默认值")
            return DEFAULT_THRESHOLDS

    def compute(
        self,
        factor_scores: list[FactorScoreResult],
        factor_weights: list[float],
        buy_threshold: float = 3.5,
        sell_threshold: float = 2.0,
        thresholds_json: Optional[str] = None,
    ) -> SignalResult:
        """计算加权评分并生成信号

        因子 scores 范围 -1.0 ~ +1.0，加权求和后为
        [-总权重, +总权重]，总权重 ≈ 6.0 → [-6.0, +6.0]。

        Args:
            factor_scores: 因子评分结果列表（score ∈ [-1, +1]）
            factor_weights: 因子权重列表
            thresholds_json: 阈值配置 JSON

        Returns:
            SignalResult
        """
        if not factor_scores or not factor_weights:
            logger.warning("因子评分或权重为空，返回中性信号")
            return SignalResult(
                weighted_score=0.0,
                raw_score=0.0,
                signal_direction="hold",
                signal_strength="hold",
                operation_advice="数据不足，建议观望",
                equity_ratio=0.5,
            )

        # 计算加权总分（-1~+1 因子分值的加权求和）
        weighted_sum = 0.0
        for score, weight in zip(factor_scores, factor_weights):
            weighted_sum += score.score * weight

        raw_score = weighted_sum
        # 钳位到理论范围：用户 7 因子总权重 6.5 + 市场环境 3 因子 1.8 = 8.3
        # （旧 8 因子体系 6.4；钳位放宽不影响旧因子集的分数分布）
        normalized = round(max(-8.5, min(8.5, weighted_sum)), 2)

        # 加载阈值配置并判定信号
        thresholds = self._load_thresholds(thresholds_json)
        direction, strength, advice, equity = self._determine_signal(normalized, thresholds)

        return SignalResult(
            weighted_score=normalized,
            raw_score=round(raw_score, 4),
            signal_direction=direction,
            signal_strength=strength,
            operation_advice=advice,
            equity_ratio=equity,
        )

    def _determine_signal(
        self,
        score: float,
        thresholds: list[dict],
    ) -> tuple[str, str, str, float]:
        """判定信号方向和强度

        遍历阈值配置（降序），找到第一个满足 score >= min_score 的档位。
        """
        for tier in thresholds:
            if score >= tier["min_score"]:
                advice = tier["operation_advice"].format(
                    score=score,
                    equity_pct=int(tier["equity_ratio"] * 100),
                )
                return (
                    tier["signal_direction"],
                    tier["signal_strength"],
                    advice,
                    tier["equity_ratio"],
                )

        # 末档兜底（正常情况下不会到达）
        last = thresholds[-1] if thresholds else DEFAULT_THRESHOLDS[-1]
        advice = last["operation_advice"].format(
            score=score,
            equity_pct=int(last["equity_ratio"] * 100),
        )
        return (last["signal_direction"], last["signal_strength"], advice, last["equity_ratio"])


# 全局引擎实例
scoring_engine = ScoringEngine()


def compute_with_quality_filter(
    factor_scores: list[FactorScoreResult],
    factor_weights: list[float],
    quality_result,
    thresholds_json: Optional[str] = None,
) -> SignalResult:
    """评分 + 质量过滤器集成入口

    原有 ScoringEngine.compute 完全不变。此函数作为外围包装器，
    先调用 compute 得到基础评分（weighted_score / raw_score），再根据质量过滤
    结果调整最终决策。

    决策路径说明（2026-08-22 审计修复）：
    - 五档阈值（DEFAULT_THRESHOLDS / thresholds_json）在此路径不生效，仅用于
      无质量过滤的旧路径（ScoringEngine.compute 直接调用）。
    - 本路径最终信号 direction/strength 由 determine_signal 动态阈值决定
      （base_buy/sell_threshold + 规模冲击/漂移上调）。
    - base_signal 仍需调用以获取 weighted_score 和 raw_score（钳位后的原始评分）。

    Args:
        factor_scores: 已修正的因子评分列表
        factor_weights: 已修正的因子权重列表
        quality_result: QualityFilterResult 质量过滤结果
        thresholds_json: 阈值配置 JSON（仅用于 base_signal 的原始评分计算，不影响最终决策）

    Returns:
        SignalResult（含扩展字段）
    """
    # 1. 用原有 compute 得到基础信号
    base_signal = scoring_engine.compute(
        factor_scores=factor_scores,
        factor_weights=factor_weights,
        thresholds_json=thresholds_json,
    )

    original_score = base_signal.weighted_score

    # 2. 加偏置得到最终评分
    adjusted_score = original_score + quality_result.institution_bias
    adjusted_score = round(max(-8.5, min(8.5, adjusted_score)), 2)

    # 3. 动态阈值决策
    from backend.engines.quality_filter import determine_signal
    direction, strength, warning = determine_signal(
        adjusted_score,
        quality_result.dynamic_buy_threshold,
        quality_result.dynamic_sell_threshold,
        quality_result.drift_triggered,
    )

    # 4. 生成操作建议
    equity_map = {
        "heavy_buy": 0.9, "moderate_buy": 0.7,
        "hold": 0.5,
        "moderate_sell": 0.3, "heavy_sell": 0.1,
    }
    equity = equity_map.get(strength, 0.5)
    advice = f"综合评分 {adjusted_score}（原始 {original_score} + 偏置 {quality_result.institution_bias:+.1f}），"
    if direction == "buy":
        advice += f"建议加仓，权益仓位可升至 {int(equity * 100)}%"
    elif direction == "sell":
        advice += f"建议减仓，权益仓位降至 {int(equity * 100)}%"
    else:
        advice += f"建议持有观望，维持基准仓位 {int(equity * 100)}%"

    # 5. 汇总警告（去重：build_result 与 determine_signal 对漂移会生成相同文案）
    warnings = list(quality_result.warnings)
    if warning and warning not in warnings:
        warnings.append(warning)

    return SignalResult(
        weighted_score=adjusted_score,
        raw_score=base_signal.raw_score,
        signal_direction=direction,
        signal_strength=strength,
        operation_advice=advice,
        equity_ratio=equity,
        original_score=round(original_score, 4),
        dynamic_buy_threshold=quality_result.dynamic_buy_threshold,
        quality_warnings=warnings,
    )
