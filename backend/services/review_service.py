"""投资复盘服务 — 组合区间收益复盘（原生实现，复用库内基金池 + 公开净值接口）

与 SkillHub investment-review 的差异：无需用户提供持仓截图/运行第三方脚本，
基金池即"组合"，净值/评分/信号全部来自库内数据与既有数据源链路。

计算口径（等权买入持有，期间无调仓假设）：
- 单基金区间涨跌 = nav_end / nav_start - 1
  （nav_start = 起始日或之前最近一个净值日；nav_end 同理）
  场外净值取**分红复权**序列（Q11-A，与因子链/回测同口径）：裸单位净值在除息日
  一次性扣掉分红，会把有分红的基金记成一天真实下跌。ETF 分支本来就是 qfq。
  开关 `review_nav_adjusted=0` 可切回单位净值
- 组合收益 = mean(各基金区间涨跌)（等权）
- 基准 = 沪深300 价格指数 + 可配股息率（Q11-B，默认 2.7%/年，按区间交易日折算）。
  基金侧含分红而基准不含，等于白记约 2.7pp/年 的"超额"
- 信号复盘：区间首日前最近一次分析信号 vs 区间实际涨跌，绝对与超额两个同向率都出
  （绝对口径在上涨市里近乎恒真，量的是 beta；超端口径才量得出选基能力）

生效口径随报告一起返回（`report.caliber` + 三行口径头），三处消费方共用
`caliber_service`，见 docs/QUANT_DECISIONS_2026-10.md §5.1。
"""

import logging
import math
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_sources.base import guess_fund_type
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.schemas.analysis import FundReviewItem, ReviewReport
from backend.utils.timezone import beijing_today

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
        from backend.services.caliber_service import caliber_head_lines, load_caliber

        # 一轮复盘一个口径（Q11）：逐只重读配置会让中途改口径的同轮结果不可比
        policy = await load_caliber(self.db)
        caliber_lines = caliber_head_lines(
            policy,
            extra="组合按基金池等权买入持有",
            cash_line="满仓假设，不涉及现金利息",
        )

        adapter = AKShareAdapter()

        async def _fetch(fund: Fund):
            try:
                series = await _fetch_nav_series(
                    adapter, fund.code, fetch_days,
                    start_date=start_date, adjusted=policy["nav_adjusted"],
                )
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

        benchmark = await self._benchmark_growth(
            start_date, end_date, policy["bench_div_yield_pct"])
        excess = (
            round(portfolio - benchmark, 2)
            if portfolio is not None and benchmark is not None
            else None
        )

        best = max(valid, key=lambda x: x.growth_pct) if valid else None
        worst = min(valid, key=lambda x: x.growth_pct) if valid else None
        signal_stats = self._signal_hit_stats(valid, benchmark)

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
            caliber={**policy, "lines": caliber_lines},
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
    def _signal_hit_stats(
        items: list[FundReviewItem], benchmark_growth_pct: Optional[float] = None
    ) -> dict:
        """区间首日前信号与区间实际涨跌的同向率（buy 涨为命中，sell 跌为命中）

        Q11：只报绝对涨跌会把上涨市里近乎恒真的命中率当成选基能力（量的是 beta）。
        因此同时给出**超额口径**（个基区间涨跌 − 基准同区间涨跌），`hit_rate` 仍是
        绝对口径（旧字段/旧前端语义不变），超额口径挂在 `excess` 子字典下。
        """
        stats = {"buy_total": 0, "buy_hits": 0, "sell_total": 0, "sell_hits": 0}
        excess = {"buy_total": 0, "buy_hits": 0, "sell_total": 0, "sell_hits": 0}

        def _rate(b: dict) -> Optional[float]:
            total = b["buy_total"] + b["sell_total"]
            hits = b["buy_hits"] + b["sell_hits"]
            return round(hits / total * 100, 1) if total else None

        for it in items:
            if it.growth_pct is None or it.signal_start is None:
                continue
            if it.signal_start == "buy":
                bucket, sign = "buy", 1.0
            elif it.signal_start == "sell":
                bucket, sign = "sell", -1.0
            else:
                continue
            stats[f"{bucket}_total"] += 1
            if sign * it.growth_pct > 0:
                stats[f"{bucket}_hits"] += 1
            if benchmark_growth_pct is not None:
                excess[f"{bucket}_total"] += 1
                if sign * (it.growth_pct - benchmark_growth_pct) > 0:
                    excess[f"{bucket}_hits"] += 1

        stats["hit_rate"] = _rate(stats)
        if benchmark_growth_pct is not None:
            excess["hit_rate"] = _rate(excess)
            excess["benchmark_growth_pct"] = benchmark_growth_pct
            stats["excess"] = excess
        return stats

    async def _benchmark_growth(
        self, start_date: str, end_date: str, dividend_yield_pct: float = 0.0
    ) -> Optional[float]:
        """沪深300 同区间涨跌（价格指数 + 按区间交易日折算的股息，Q11-B）"""
        try:
            from backend.data_sources.akshare_adapter import AKShareAdapter
            from backend.services.caliber_service import with_dividend_carry
            adapter = AKShareAdapter()
            # 2026-09-12 复查：复用 adapter 基准缓存（原先直连绕过 1h 缓存）
            series = with_dividend_carry(
                await adapter.get_benchmark_series(), dividend_yield_pct)
            if not series:
                return None
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
        ]
        # Q11-C：三行口径头固定在最前，先看尺子再看数字
        lines += list(r.caliber.get("lines") or []) + [
            "> 仅供参考，不构成投资建议。",
            "",
            "### 一句话总结",
        ]
        if r.portfolio_growth_pct is not None:
            vs = (
                f"，{'跑赢' if r.excess_pct >= 0 else '跑输'}基准 {abs(r.excess_pct)}pp"
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
                f"- 信号复盘（绝对口径）：区间首日信号命中率 **{ss['hit_rate']}%**"
                f"（buy {ss['buy_hits']}/{ss['buy_total']}，sell {ss['sell_hits']}/{ss['sell_total']}）"
            )
        ex = ss.get("excess") or {}
        if ex.get("hit_rate") is not None:
            lines.append(
                f"- 信号复盘（超额口径，基准 {ex['benchmark_growth_pct']:+.2f}%）：命中率 **{ex['hit_rate']}%**"
                f"（buy {ex['buy_hits']}/{ex['buy_total']}，sell {ex['sell_hits']}/{ex['sell_total']}）"
                "—— 绝对口径量的是市场方向（beta），这一行才量得出选基能力"
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
    adapter, code: str, days: int, start_date: Optional[str] = None,
    *, adjusted: bool = True,
) -> list[tuple[str, float]]:
    """按基金类型拉取日频净值/收盘序列（升序 [(date, nav)]）

    start_date 给定时 cutoff 锚定其前 60 天：旧实现 cutoff=today()-days 只
    覆盖"距今 days 天"，复盘任何结束日早于今天 >days 的历史区间时起点净值
    缺失，整段收益对比恒为 None。

    adjusted=True（默认，Q11-A）时场外净值是**分红复权**序列，与因子链/回测
    同源同口径；`review_nav_adjusted=0` 时调用方传 False 回到裸单位净值。
    """
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
        # 场外净值改走全量分析同一条链路：pingzhongdata 纯 Python 解析 → f10/lsjz 兜底。
        # 原实现直连 akshare fund_open_fund_info_em（需 py_mini_racer 执行 JS），
        # V8 报错时这里没有任何退路，整只基金复盘直接无数据（2026-09-24 该错误每次必报）。
        df = await adapter._get_otc_nav_from_js(code)
        if df is None or df.empty:
            df = await adapter._get_otc_fund_nav_raw(code, period=max(int(days * 1.5), 60))
        if df is None or df.empty:
            return []
        raw = otc_nav_pairs(df, adjusted=adjusted)

    series = sorted(raw)
    if start_date:
        cutoff = (date.fromisoformat(start_date) - timedelta(days=60)).isoformat()
    else:
        cutoff = (beijing_today() - timedelta(days=days)).isoformat()
    return [(d, v) for d, v in series if d >= cutoff]


def otc_nav_pairs(df, *, adjusted: bool = True) -> list[tuple[str, float]]:
    """净值 DataFrame（净值日期 / 单位净值 [/ 日增长率]）→ [(日期, 净值)] 升序

    adjusted 时按同日 日增长率 做分红复权（复用因子链同一个
    `build_forward_adjusted_nav`）：裸单位净值在除息日一次性扣掉分红，
    复盘/回填于是把有分红的基金记成一天暴跌（Q11-A，第一批 #56 的遗留）。
    备源只有 DWJZ 没有 日增长率 时 helper 原样返回，不会"复权后反而更差"。
    """
    from backend.data_sources.akshare_adapter import build_forward_adjusted_nav

    rows: list[tuple[str, float, object]] = []
    growth_col = df["日增长率"] if "日增长率" in df.columns else [None] * len(df)
    for d, v, g in zip(df["净值日期"], df["单位净值"], growth_col):
        # 停牌日 DWJZ 为空/NaN：整行剔除（留在序列里会让复权链出现 0 或 NaN 值）
        try:
            nav = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(nav) or nav <= 0:
            continue
        rows.append((str(d)[:10], nav, g))

    if not rows:
        return []
    if not adjusted:
        return [(d, v) for d, v, _ in rows]
    adjusted_nav = build_forward_adjusted_nav(
        [v for _, v, _ in rows], [g for _, _, g in rows]
    )
    return [(d, round(v, 6)) for d, v in zip([d for d, _, _ in rows], adjusted_nav)]
