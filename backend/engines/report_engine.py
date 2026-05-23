from __future__ import annotations
"""报告生成引擎 — 根据配置项组合输出报告内容

报告配置项（基金维度）：
1. factor_detail      — 因子详情
2. weighted_score     — 加权评分
3. operation_advice   — 操作建议
4. signal_strength    — 信号强度
5. risk_warning       — 风险提示

报告配置项（市场维度）：
6. signal_summary     — 信号概览
7. top_buy_sell       — TOP5 买卖信号
8. adv_decline        — 涨跌分布
9. turnover           — 两市成交额
10. market_flow       — 大盘资金流
11. hsgt_flow         — 沪深港通
12. sector_flow_day   — 板块资金流(当日)
13. sector_flow_week  — 板块资金流(周)
14. sector_flow_month — 板块资金流(月)
"""

import logging
from datetime import date
from typing import Optional

from backend.engines.factor_engine import FactorScoreResult
from backend.engines.scoring_engine import SignalResult
from backend.schemas.market import MarketSummaryOut

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
            lines.append("| 因子 | 原始值 | 评分(-1~+1) | 方向 |")
            lines.append("|------|--------|-------------|------|")
            for fs in factor_scores:
                direction_label = "正向" if fs.direction == "positive" else "反向"
                lines.append(f"| {fs.factor_name} | {fs.raw_value} | {fs.score} | {direction_label} |")
            lines.append("")

        # 加权评分
        if "weighted_score" in enabled_items:
            lines.append("## 加权评分")
            lines.append("")
            score_bar = self._score_bar(signal.weighted_score)
            lines.append(f"**综合评分**: {score_bar} {signal.weighted_score}（-6.0 ~ +6.0）")
            lines.append(f"**建议权益仓位**: {int(signal.equity_ratio * 100)}%")
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
        """评分进度条（10 格，-6.0 ~ +6.0 映射到 0-10 格）"""
        segments = int(round((score + 6.0) / 12.0 * 10))
        segments = max(0, min(10, segments))
        filled = segments
        empty = 10 - filled
        return "█" * filled + "░" * empty

    def _strength_label(self, strength: str) -> str:
        """信号强度中文标签"""
        labels = {
            "heavy_buy": "🔴🔴🔴 强烈加仓（权益仓位90%）",
            "moderate_buy": "🔴🔴 适度加仓（权益仓位70%）",
            "hold": "⚪ 中性观望（基准仓位50%）",
            "moderate_sell": "🟢🟢 适度减仓（权益仓位30%）",
            "heavy_sell": "🟢🟢🟢 强烈减仓（权益仓位10%）",
        }
        return labels.get(strength, strength)

    def _generate_risk_warning(
        self,
        signal: SignalResult,
        factor_scores: list[FactorScoreResult],
    ) -> str:
        """生成风险提示文本"""
        warnings: list[str] = []

        # 检查是否有因子数据不足（score=0 且 raw_value=0 表示缺失）
        low_score_factors = [fs for fs in factor_scores if fs.score == 0.0 and fs.raw_value == 0.0]
        if low_score_factors:
            names = ", ".join(fs.factor_name for fs in low_score_factors)
            warnings.append(f"以下因子数据不足，评分可能不准确：{names}")

        # 检查评分极端情况（-6.0 ~ +6.0 范围）
        if signal.weighted_score >= 4.0:
            warnings.append("评分偏高，注意追高风险，建议分批建仓")
        elif signal.weighted_score <= -4.0:
            warnings.append("评分极低，可能存在系统性风险，谨慎操作")

        # 检查因子评分分歧（-1~+1 范围，2.0 为满幅）
        scores = [fs.score for fs in factor_scores]
        if scores and (max(scores) - min(scores)) > 1.5:
            warnings.append("因子评分分歧较大，信号可靠性降低，建议综合判断")

        if not warnings:
            warnings.append("当前无明显风险信号，但仍需关注市场变化")

        return "\n".join(f"- {w}" for w in warnings)


    def generate_market_summary_markdown(
        self,
        market_summary: MarketSummaryOut,
        enabled_items: list[str] | None = None,
    ) -> str:
        """生成市场概况 Markdown

        Args:
            market_summary: 市场概况数据
            enabled_items: 启用的报告项列表

        Returns:
            Markdown 文本
        """
        if enabled_items is None:
            enabled_items = [
                "signal_summary", "top_buy_sell", "adv_decline", "turnover",
                "market_flow", "hsgt_flow",
                "sector_flow_day", "sector_flow_week", "sector_flow_month",
            ]

        lines: list[str] = []
        ms = market_summary

        # ── 信号概览 ──
        if "signal_summary" in enabled_items:
            sig = ms.signals
            lines.append("## 📊 信号概览")
            lines.append("")
            lines.append(f"**买入**: {sig.buy_count} 只 | **持有**: {sig.hold_count} 只 | **卖出**: {sig.sell_count} 只")
            lines.append(f"**总计**: {sig.total} 只基金")
            lines.append("")

        # ── TOP5 买卖信号 ──
        if "top_buy_sell" in enabled_items:
            lines.append("## 🔴 TOP5 买入信号")
            lines.append("")
            lines.append("| 基金 | 代码 | 评分 | 强度 |")
            lines.append("|------|------|------|------|")
            for r in ms.signals.top_buy:
                lines.append(f"| {r.fund_name} | {r.fund_code} | {r.weighted_score} | {r.signal_strength} |")
            lines.append("")
            lines.append("## 🟢 TOP5 卖出信号")
            lines.append("")
            lines.append("| 基金 | 代码 | 评分 | 强度 |")
            lines.append("|------|------|------|------|")
            for r in ms.signals.top_sell:
                lines.append(f"| {r.fund_name} | {r.fund_code} | {r.weighted_score} | {r.signal_strength} |")
            lines.append("")

        # ── 涨跌分布 ──
        if "adv_decline" in enabled_items and ms.adv_decline:
            ad = ms.adv_decline
            up_pct = ad.up_count / ad.total_count * 100 if ad.total_count > 0 else 0
            lines.append("## 📈 涨跌分布")
            lines.append("")
            lines.append(f"**上涨**: {ad.up_count} 只 ({up_pct:.1f}%) | **下跌**: {ad.down_count} 只 | **总计**: {ad.total_count} 只")
            lines.append("")

        # ── 两市成交额 ──
        if "turnover" in enabled_items and ms.turnover:
            t = ms.turnover
            change_sign = "+" if t.change_pct >= 0 else ""
            lines.append("## 💰 两市成交额")
            lines.append("")
            lines.append(f"**沪市**: {t.sse_amount:,.0f} 亿")
            lines.append(f"**深市**: {t.szse_amount:,.0f} 亿")
            lines.append(f"**合计**: {t.total_amount:,.0f} 亿")
            lines.append(f"**较上日**: {change_sign}{t.change_pct}%")
            lines.append("")

        # ── 大盘资金流 ──
        if "market_flow" in enabled_items and ms.market_flow:
            mf = ms.market_flow
            flow = mf.main_flow
            lines.append("## 🏦 大盘资金流")
            lines.append("")
            lines.append(f"日期：{mf.date}")
            lines.append(f"上证：{mf.sh_index}（{mf.sh_change}%） | 深证：{mf.sz_index}（{mf.sz_change}%）")
            lines.append(f"**主力净流入**: {flow.net_amount:,.2f} 亿（占比 {flow.net_ratio}%）")
            lines.append(f"超大单：{flow.super_large_net:,.2f} 亿 | 大单：{flow.large_net:,.2f} 亿")
            lines.append(f"中单：{flow.medium_net:,.2f} 亿 | 小单：{flow.small_net:,.2f} 亿")
            lines.append("")

        # ── 沪深港通 ──
        if "hsgt_flow" in enabled_items and ms.hsgt_flow:
            h = ms.hsgt_flow
            lines.append("## 🌐 沪深港通")
            lines.append("")
            lines.append(f"**北向资金**: {h.north_net_buy:,.2f} 亿")
            lines.append(f"**南向资金**: {h.south_net_buy:,.2f} 亿")
            lines.append(f"日期：{h.date}")
            lines.append("")

        # ── 板块资金流 ──
        sector_map = {"sector_flow_day": "当日", "sector_flow_week": "周", "sector_flow_month": "月"}
        for item_key, tf_label in sector_map.items():
            if item_key not in enabled_items:
                continue
            # 找到对应时间维度
            sr = next((s for s in ms.sector_flow if s.timeframe == tf_label), None)
            if not sr:
                continue

            lines.append(f"## 🏭 板块资金流（{tf_label}）")
            lines.append("")
            lines.append("**主力流入 TOP5**")
            lines.append("")
            lines.append("| 板块 | 净流入(亿) | 涨跌幅 | 领涨股 |")
            lines.append("|------|-----------|--------|--------|")
            for i in sr.by_inflow[:5]:
                top_stock = i.top_stock if i.top_stock else "-"
                lines.append(f"| {i.sector_name} | {i.main_net_inflow:+,.2f} | {i.change_pct:+.2f}% | {top_stock} |")
            lines.append("")
            lines.append("**主力流出 TOP5**")
            lines.append("")
            lines.append("| 板块 | 净流入(亿) | 涨跌幅 |")
            lines.append("|------|-----------|--------|")
            for i in sr.by_outflow[:5]:
                lines.append(f"| {i.sector_name} | {i.main_net_inflow:+,.2f} | {i.change_pct:+.2f}% |")
            lines.append("")

        return "\n".join(lines)


# 全局引擎实例
report_engine = ReportEngine()
