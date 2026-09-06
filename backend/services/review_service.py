"""投资复盘服务 — 组合区间收益复盘（原生实现，复用库内基金池 + 公开净值接口）

与 SkillHub investment-review 的差异：无需用户提供持仓截图/运行第三方脚本，
基金池即"组合"，净值/评分/信号全部来自库内数据与既有数据源链路。

计算口径（等权买入持有，期间无调仓假设）：
- 单基金区间涨跌 = nav_end / nav_start - 1
  （nav_start = 起始日或之前最近一个净值日；nav_end 同理）
- 组合收益 = mean(各基金区间涨跌)（等权）
- 基准 = 沪深300 官方指数点位同区间涨跌（价格回报口径）
- 信号复盘：区间首日前最近一次分析信号 vs 区间实际涨跌，统计 buy/sell 命中率
"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_sources.base import guess_fund_type
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.schemas.analysis import FundReviewItem, ReviewReport

logger = logging.getLogger(__name__)


class ReviewService:
    """投资复盘服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def review(
        self,
        start_date: str,
        end_date: str,
        fund_ids: Optional[list[int]] = None,
    ) -> ReviewReport:
        """运行组合区间复盘

        Args:
            start_date: 起始日期 YYYY-MM-DD（含）
            end_date: 结束日期 YYYY-MM-DD（含）
            fund_ids: 指定基金，空则取全部活跃基金
        """
        stmt = select(Fund).where(Fund.status == "active")
        if fund_ids:
            stmt = stmt.where(Fund.id.in_(fund_ids))
        funds = list((await self.db.execute(stmt)).scalars().all())
        if not funds:
            raise ValueError("基金池为空，无法复盘")

        # 区间合法性与上限（防止误拉超长历史）
        d_start = date.fromisoformat(start_date)
        d_end = date.fromisoformat(end_date)
        if d_end < d_start:
            raise ValueError("结束日期早于起始日期")
        days = (d_end - d_start).days
        if days > 730:
            raise ValueError("复盘区间最长 2 年")

        # 净值序列多拉 15 天 buffer（保证起点日之前有最近交易日数据）
        fetch_days = days + 30

        # 并发拉取各基金净值（复用 adapter 信号量/重试/超时链路）
        import asyncio
        from backend.data_sources.akshare_adapter import AKShareAdapter
        adapter = AKShareAdapter()

        async def _fetch(fund: Fund):
            try:
                series = await _fetch_nav_series(adapter, fund.code, fetch_days)
                return fund, series, None
            except Exception as e:
                logger.warning(f"复盘拉取净值失败 {fund.code}: {e}")
                return fund, None, str(e)[:80]

        results = await asyncio.gather(*[_fetch(f) for f in funds])

        # 逐只计算区间涨跌 + 评分信号变化
        items: list[FundReviewItem] = []
        for fund, series, err in results:
            item = FundReviewItem(
                fund_code=fund.code, fund_name=fund.name or fund.code, error=err
            )
            if series:
                nav_start, nav_end = _slice_range(series, start_date, end_date)
                if nav_start is not None and nav_end is not None and nav_start[1] > 0:
                    item.nav_start, item.nav_end = nav_start[1], nav_end[1]
                    item.growth_pct = round((nav_end[1] / nav_start[1] - 1) * 100, 2)
            s0, s1 = await asyncio.gather(
                self._score_at(fund.id, start_date),
                self._score_at(fund.id, end_date),
            )
            if s0:
                item.score_start, item.signal_start = s0[0], s0[1]
            if s1:
                item.score_end, item.signal_end = s1[0], s1[1]
            items.append(item)

        valid = [it for it in items if it.growth_pct is not None]
        portfolio = round(sum(it.growth_pct for it in valid) / len(valid), 2) if valid else None
        for it in valid:
            it.contribution_pct = round(it.growth_pct / len(valid), 3)

        benchmark = await self._benchmark_growth(start_date, end_date, fetch_days)
        excess = (
            round(portfolio - benchmark, 2)
            if portfolio is not None and benchmark is not None
            else None
        )

        best = max(valid, key=lambda x: x.growth_pct) if valid else None
        worst = min(valid, key=lambda x: x.growth_pct) if valid else None
        signal_stats = self._signal_hit_stats(valid)

        report = ReviewReport(
            start_date=start_date,
            end_date=end_date,
            fund_count=len(funds),
            portfolio_growth_pct=portfolio,
            benchmark_growth_pct=benchmark,
            excess_pct=excess,
            best=best,
            worst=worst,
            items=sorted(items, key=lambda x: (x.growth_pct is None, -(x.growth_pct or 0))),
            signal_stats=signal_stats,
        )
        report.summary_md = self._build_summary_md(report)
        return report

    # ── 内部 ────────────────────────────────────────────────────────────

    async def _score_at(self, fund_id: int, day: str) -> Optional[tuple[float, str]]:
        """day 当日或之前最近一次分析的 (评分, 信号方向)"""
        r = await self.db.execute(
            select(AnalysisResult)
            .where(
                AnalysisResult.fund_id == fund_id,
                AnalysisResult.analysis_date <= day,
            )
            .order_by(AnalysisResult.analysis_date.desc())
            .limit(1)
        )
        ar = r.scalars().first()
        if ar is None:
            return None
        return (ar.weighted_score, ar.signal_direction or "hold")

    @staticmethod
    def _signal_hit_stats(items: list[FundReviewItem]) -> dict:
        """区间首日前信号与实际涨跌的同向率（buy 涨为命中，sell 跌为命中）"""
        stats = {"buy_total": 0, "buy_hits": 0, "sell_total": 0, "sell_hits": 0}
        for it in items:
            if it.growth_pct is None or it.signal_start is None:
                continue
            if it.signal_start == "buy":
                stats["buy_total"] += 1
                if it.growth_pct > 0:
                    stats["buy_hits"] += 1
            elif it.signal_start == "sell":
                stats["sell_total"] += 1
                if it.growth_pct < 0:
                    stats["sell_hits"] += 1
        total = stats["buy_total"] + stats["sell_total"]
        hits = stats["buy_hits"] + stats["sell_hits"]
        stats["hit_rate"] = round(hits / total * 100, 1) if total else None
        return stats

    async def _benchmark_growth(self, start_date: str, end_date: str, fetch_days: int) -> Optional[float]:
        """沪深300 官方指数同区间涨跌（价格回报口径）"""
        try:
            from backend.data_sources.akshare_adapter import AKShareAdapter
            adapter = AKShareAdapter()

            def _fetch():
                import akshare as ak
                df = ak.stock_zh_index_daily(symbol="sh000300")
                return df

            df = await adapter._call(_fetch)
            if df is None or df.empty:
                return None
            series = sorted(
                (str(d)[:10], float(c))
                for d, c in zip(df["date"], df["close"])
            )
            s0 = _nearest_on_or_before(series, start_date)
            s1 = _nearest_on_or_before(series, end_date)
            if s0 and s1 and s0[1] > 0:
                return round((s1[1] / s0[1] - 1) * 100, 2)
        except Exception as e:
            logger.warning(f"沪深300 基准获取失败: {e}")
        return None

    @staticmethod
    def _build_summary_md(r: ReviewReport) -> str:
        """生成 Markdown 复盘报告（页面展示 + 可直接喂 AI 解读）"""
        lines = [
            f"## 📋 投资复盘报告（{r.start_date} → {r.end_date}）",
            "",
            f"> 口径：基金池等权买入持有，期间无调仓假设；基准为沪深300（价格回报）。仅供参考，不构成投资建议。",
            "",
            "### 一句话总结",
        ]
        if r.portfolio_growth_pct is not None:
            vs = (
                f"，{'跑赢' if r.excess_pct >= 0 else '跑输'}沪深300 {abs(r.excess_pct)}pp"
                if r.excess_pct is not None else ""
            )
            lines.append(
                f"组合区间收益 **{r.portfolio_growth_pct:+.2f}%**{vs}，"
                f"覆盖 {r.fund_count} 只基金（有效 {len([i for i in r.items if i.growth_pct is not None])} 只）"
            )
        else:
            lines.append("有效净值数据不足，未能计算组合收益")
        if r.best:
            lines.append(f"- 最大贡献：**{r.best.fund_name}**({r.best.fund_code}) {r.best.growth_pct:+.2f}%")
        if r.worst:
            lines.append(f"- 最大拖累：**{r.worst.fund_name}**({r.worst.fund_code}) {r.worst.growth_pct:+.2f}%")
        ss = r.signal_stats
        if ss.get("hit_rate") is not None:
            lines.append(
                f"- 信号复盘：区间首日信号命中率 **{ss['hit_rate']}%**"
                f"（buy {ss['buy_hits']}/{ss['buy_total']}，sell {ss['sell_hits']}/{ss['sell_total']}）"
            )
        lines += ["", "### 区间涨跌明细", "",
                  "| 基金 | 区间涨跌 | 评分变化 | 信号(始→末) |",
                  "|------|----------|----------|-------------|"]
        for it in r.items:
            growth = f"{it.growth_pct:+.2f}%" if it.growth_pct is not None else "—"
            score = (
                f"{it.score_start}→{it.score_end}"
                if it.score_start is not None and it.score_end is not None
                else "—"
            )
            sig = f"{it.signal_start or '—'}→{it.signal_end or '—'}"
            lines.append(f"| {it.fund_name}({it.fund_code}) | {growth} | {score} | {sig} |")
        return "\n".join(lines)


# ── 模块级纯函数（可单测）─────────────────────────────────────────────

def _nearest_on_or_before(
    series: list[tuple[str, float]], day: str
) -> Optional[tuple[str, float]]:
    """day 当日或之前最近的数据点；series 需按日期升序"""
    best = None
    for d, v in series:
        if d <= day:
            best = (d, v)
        else:
            break
    return best


def _slice_range(
    series: list[tuple[str, float]], start_date: str, end_date: str
) -> tuple[Optional[tuple[str, float]], Optional[tuple[str, float]]]:
    """取区间起止点（当日或之前最近净值日）"""
    return (
        _nearest_on_or_before(series, start_date),
        _nearest_on_or_before(series, end_date),
    )


async def _fetch_nav_series(
    adapter, code: str, days: int
) -> list[tuple[str, float]]:
    """按基金类型拉取日频净值/收盘序列（升序 [(date, nav)]）"""
    import akshare as ak

    if guess_fund_type(code) == "etf":
        def _etf():
            df = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="qfq")
            return [
                (str(d)[:10], float(c))
                for d, c in zip(df["日期"], df["收盘"])
            ]
        raw = await adapter._call(_etf, _max_attempts=2)
    else:
        def _otc():
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            return [
                (str(d)[:10], float(v))
                for d, v in zip(df["净值日期"], df["单位净值"])
            ]
        raw = await adapter._call(_otc, _max_attempts=2)

    series = sorted(raw)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [(d, v) for d, v in series if d >= cutoff]
