from __future__ import annotations
"""报告生成引擎 — 根据配置项组合输出报告内容

报告配置项：
1. factor_detail   — 因子详情
2. weighted_score  — 加权评分
3. operation_advice — 操作建议
4. signal_strength — 信号强度
5. risk_warning    — 风险提示
"""

import logging
from datetime import date
from typing import Optional

from backend.engines.factor_engine import FactorScoreResult
from backend.engines.scoring_engine import SignalResult

logger = logging.getLogger(__name__)


class ReportEngine:
    """报告生成引擎"""

    def generate_markdown(
        self,
        fund_code: str,
        fund_name: str,
        analysis_date: str,
        signal: SignalResult,
        factor_scores: list[FactorScoreResult],
        enabled_items: list[str] | None = None,
    ) -> str:
        """生成 Markdown 格式报告

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            analysis_date: 分析日期
            signal: 信号结果
            factor_scores: 因子评分列表
            enabled_items: 启用的报告项列表，None 表示全部

        Returns:
            Markdown 文本
        """
        if enabled_items is None:
            enabled_items = [
                "factor_detail",
                "weighted_score",
                "operation_advice",
                "signal_strength",
                "risk_warning",
            ]

        lines: list[str] = []

        # 报告标题
        signal_emoji = self._signal_emoji(signal.signal_direction)
        lines.append(f"# {signal_emoji} {fund_name}({fund_code}) 量化分析报告")
        lines.append(f"**分析日期**: {analysis_date}")
        lines.append("")

        # 因子详情
        if "factor_detail" in enabled_items:
            lines.append("## 因子详情")
            lines.append("")
            lines.append("| 因子 | 原始值 | 评分(0-5) | 方向 |")
            lines.append("|------|--------|-----------|------|")
            for fs in factor_scores:
                direction_label = "正向" if fs.direction == "positive" else "反向"
                lines.append(f"| {fs.factor_name} | {fs.raw_value} | {fs.score} | {direction_label} |")
            lines.append("")

        # 加权评分
        if "weighted_score" in enabled_items:
            lines.append("## 加权评分")
            lines.append("")
            score_bar = self._score_bar(signal.weighted_score)
            lines.append(f"**综合评分**: {score_bar} {signal.weighted_score}/5.0")
            lines.append("")

        # 操作建议
        if "operation_advice" in enabled_items:
            lines.append("## 操作建议")
            lines.append("")
            lines.append(f"{signal.operation_advice}")
            lines.append("")

        # 信号强度
        if "signal_strength" in enabled_items:
            lines.append("## 信号强度")
            lines.append("")
            strength_label = self._strength_label(signal.signal_strength)
            lines.append(f"**信号方向**: {signal.signal_direction.upper()}")
            lines.append(f"**信号强度**: {strength_label}")
            lines.append("")

        # 风险提示
        if "risk_warning" in enabled_items:
            lines.append("## 风险提示")
            lines.append("")
            risk_text = self._generate_risk_warning(signal, factor_scores)
            lines.append(risk_text)
            lines.append("")

        lines.append("---")
        lines.append("*本报告由基金量化交易系统自动生成，仅供参考，不构成投资建议。*")

        return "\n".join(lines)

    def generate_html(
        self,
        fund_code: str,
        fund_name: str,
        analysis_date: str,
        signal: SignalResult,
        factor_scores: list[FactorScoreResult],
        enabled_items: list[str] | None = None,
    ) -> str:
        """生成 HTML 格式报告（用于飞书卡片等场景）

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            analysis_date: 分析日期
            signal: 信号结果
            factor_scores: 因子评分列表
            enabled_items: 启用的报告项列表

        Returns:
            HTML 文本
        """
        # HTML 报告用于飞书卡片消息，实际由 feishu.py 处理
        # 此处提供简单 HTML 版本
        md_content = self.generate_markdown(
            fund_code, fund_name, analysis_date, signal, factor_scores, enabled_items
        )
        # 将 Markdown 粗略转为 HTML（飞书卡片使用自己的 JSON 格式，此处为后备方案）
        html = md_content.replace("\n", "<br>")
        html = html.replace("## ", "<h2>")
        html = html.replace("# ", "<h1>")
        html = html.replace("**", "<strong>")
        html = html.replace("| ", "<td>")
        html = html.replace(" |", "</td>")
        return html

    def _signal_emoji(self, direction: str) -> str:
        """信号方向对应 emoji"""
        emoji_map = {
            "buy": "🔴",     # 红涨
            "sell": "🟢",    # 绿跌
            "hold": "⚪",    # 灰观望
        }
        return emoji_map.get(direction, "⚪")

    def _score_bar(self, score: float) -> str:
        """评分进度条（5 格）"""
        filled = int(round(score))
        empty = 5 - filled
        return "█" * filled + "░" * empty

    def _strength_label(self, strength: str) -> str:
        """信号强度中文标签"""
        labels = {
            "heavy_buy": "🔴🔴🔴 强烈买入",
            "moderate_buy": "🔴🔴 适度买入",
            "light_buy": "🔴 轻仓买入",
            "hold": "⚪ 观望持有",
            "light_sell": "🟢 轻仓减仓",
            "moderate_sell": "🟢🟢 适度减仓",
            "heavy_sell": "🟢🟢🟢 强烈减仓",
        }
        return labels.get(strength, strength)

    def _generate_risk_warning(
        self,
        signal: SignalResult,
        factor_scores: list[FactorScoreResult],
    ) -> str:
        """生成风险提示文本"""
        warnings: list[str] = []

        # 检查是否有因子数据不足
        low_score_factors = [fs for fs in factor_scores if fs.score == 2.5 and fs.raw_value == 0.0]
        if low_score_factors:
            names = ", ".join(fs.factor_name for fs in low_score_factors)
            warnings.append(f"以下因子数据不足，评分可能不准确：{names}")

        # 检查评分极端情况
        if signal.weighted_score >= 4.5:
            warnings.append("评分极高，注意追高风险，建议分批建仓")
        elif signal.weighted_score <= 1.0:
            warnings.append("评分极低，可能存在系统性风险，谨慎操作")

        # 检查因子评分分歧
        scores = [fs.score for fs in factor_scores]
        if scores and (max(scores) - min(scores)) > 3.0:
            warnings.append("因子评分分歧较大，信号可靠性降低，建议综合判断")

        if not warnings:
            warnings.append("当前无明显风险信号，但仍需关注市场变化")

        return "\n".join(f"- {w}" for w in warnings)


# 全局引擎实例
report_engine = ReportEngine()
