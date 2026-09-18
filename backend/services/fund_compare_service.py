"""基金 PK 服务 — 多基金业绩/风险/归因对比（WorkBuddy 4+5+6 融合）

指标（对齐 WorkBuddy fund_full_report 口径，无风险利率 2%）:
- 每只基金两个窗口：近2年 + 成立以来（序列上限 5 年）
- 年化收益 / 最大回撤（净值比率口径）/ 夏普 = (年化-rf)/年化波动
- 基准归因（默认沪深300 官方指数）：Beta=cov/var、Alpha 年化（CAPM）、
  信息比率 IR = 均值超额/跟踪误差 ×√252
- 规模/持有人（模块 6）：份额/规模变化倍数（首末季报比）+ 最新机构占比
  （库内 fund_quarterly，零新增请求）

数据：净值序列复用 review_service._fetch_nav_series（限流链路），
基准为 stock_zh_index_daily sh000300。
"""

import logging
import math
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund import Fund
from backend.models.fund_quarterly import FundQuarterly
from backend.schemas.analysis import CompareReport, FundCompareItem, FundCompareMetrics

logger = logging.getLogger(__name__)

RF_ANNUAL = 2.0  # 无风险利率 %（对齐 WorkBuddy）


def annualized_return_pct(values: list[float], days: int) -> Optional[float]:
    """区间年化收益（几何）"""
    if len(values) < 2 or days <= 0 or values[0] <= 0:
        return None
    total = values[-1] / values[0] - 1
    years = days / 365.0
    if years <= 0 or total <= 0:
        return None
    return round(((1 + total) ** (1 / years) - 1) * 100, 2)


def max_drawdown_pct(values: list[float]) -> Optional[float]:
    """最大回撤（净值比率口径，%负值）"""
    if len(values) < 2:
        return None
    peak = values[0]
    worst = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = v / peak - 1
            if dd < worst:
                worst = dd
    return round(worst * 100, 2)


def _daily_returns(values: list[float]) -> list[float]:
    out = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            out.append(values[i] / values[i - 1] - 1)
    return out


def sharpe_ratio(values: list[float]) -> Optional[float]:
    """夏普 = (年化收益 - rf) / 年化波动"""
    rets = _daily_returns(values)
    if len(rets) < 20:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
    vol_annual = math.sqrt(var) * math.sqrt(252)
    if vol_annual <= 0:
        return None
    total = values[-1] / values[0] - 1
    years = len(rets) / 252.0
    ann = (1 + total) ** (1 / years) - 1 if years > 0 else 0.0
    return round((ann * 100 - RF_ANNUAL) / (vol_annual * 100), 2)


def beta_alpha_ir(
    fund_vals: list[float], bench_vals: list[float]
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """对齐日期后的 Beta / CAPM 年化 Alpha / 信息比率

    fund_vals/bench_vals：同长度、同日期序（调用方保证对齐）
    """
    if len(fund_vals) != len(bench_vals) or len(fund_vals) < 40:
        return None, None, None
    fr = _daily_returns(fund_vals)
    br = _daily_returns(bench_vals)
    n = min(len(fr), len(br))
    fr, br = fr[:n], br[:n]
    if n < 40:
        return None, None, None

    mean_f = sum(fr) / n
    mean_b = sum(br) / n
    cov = sum((fr[i] - mean_f) * (br[i] - mean_b) for i in range(n)) / (n - 1)
    var_b = sum((b - mean_b) ** 2 for b in br) / (n - 1)
    if var_b <= 0:
        return None, None, None
    beta = cov / var_b

    # CAPM 年化 Alpha
    years = n / 252.0
    ann_f = (1 + fund_vals[-1] / fund_vals[0]) ** (1 / years) - 1 if years > 0 else 0.0
    ann_b = (1 + bench_vals[-1] / bench_vals[0]) ** (1 / years) - 1 if years > 0 else 0.0
    rf = RF_ANNUAL / 100
    alpha = (ann_f - rf) - beta * (ann_b - rf)

    # 信息比率：日超额的均值/标准差 年化
    excess = [fr[i] - br[i] for i in range(n)]
    mean_e = sum(excess) / n
    var_e = sum((e - mean_e) ** 2 for e in excess) / (n - 1) if n > 1 else 0.0
    ir = (mean_e / math.sqrt(var_e)) * math.sqrt(252) if var_e > 0 else None

    return (
        round(beta, 2),
        round(alpha * 100, 2) if alpha is not None else None,
        round(ir, 2) if ir is not None else None,
    )


def align_by_dates(
    fund: list[tuple[str, float]], bench: list[tuple[str, float]]
) -> tuple[list[float], list[float]]:
    """按日期交集对齐两序列（升序输入）→ 同长 [fund_val], [bench_val]"""
    bmap = dict(bench)
    fv, bv = [], []
    for d, v in fund:
        if d in bmap:
            fv.append(v)
            bv.append(bmap[d])
    return fv, bv


class FundCompareService:
    """基金 PK 服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def compare(
        self,
        fund_ids: list[int],
        years: int = 2,
        baseline: str = "沪深300",
    ) -> CompareReport:
        from backend.data_sources.akshare_adapter import AKShareAdapter
        from backend.services.review_service import _fetch_nav_series

        if not fund_ids:
            raise ValueError("未选择基金")
        if years < 1 or years > 5:
            raise ValueError("窗口年份限 1~5")

        funds = list((await self.db.execute(
            select(Fund).where(Fund.id.in_(fund_ids))
        )).scalars().all())
        if not funds:
            raise ValueError("基金不存在")

        adapter = AKShareAdapter()
        fetch_days = 365 * 5 + 40  # 成立以来窗口上限 5 年

        # 基准序列（复用 adapter 基准缓存，2026-09-12 复查修复）
        bench_series = await adapter.get_benchmark_series()

        # 并发拉各基金净值
        import asyncio

        async def _fetch(fund: Fund):
            try:
                series = await _fetch_nav_series(adapter, fund.code, fetch_days)
                return fund, series, None
            except Exception as e:
                return fund, None, str(e)[:80]

        fetched = await asyncio.gather(*[_fetch(f) for f in funds])

        # 规模/持有人（库内季报）
        scale_inst = await self._scale_inst_map([f.id for f in funds])

        items: list[FundCompareItem] = []
        today_s = date.today().isoformat()
        start_years = (date.today() - timedelta(days=365 * years)).isoformat()

        for fund, series, err in fetched:
            item = FundCompareItem(
                fund_code=fund.code, fund_name=fund.name or fund.code, error=err
            )
            si = scale_inst.get(fund.id)
            if si:
                item.scale_growth = si["growth"]
                item.institution_pct = si["institution"]
            if series and len(series) > 30 and bench_series:
                for label, from_date in [(f"近{years}年", start_years), ("成立以来", None)]:
                    if from_date is None:
                        sub = series  # 全序列=成立以来（受拉取上限约束）
                    else:
                        sub = [p for p in series if p[0] >= from_date] or series
                    vals = [v for _, v in sub]
                    days = max(1, (len(sub) - 1)) if len(sub) > 1 else 0
                    # 近似天数用日期差
                    if len(sub) > 1:
                        from datetime import datetime as _dt
                        d0 = _dt.strptime(sub[0][0], "%Y-%m-%d")
                        d1 = _dt.strptime(sub[-1][0], "%Y-%m-%d")
                        days = max(1, (d1 - d0).days)
                    m = FundCompareMetrics(
                        window_label=label, days=days,
                        annual_return_pct=annualized_return_pct(vals, days),
                        max_drawdown_pct=max_drawdown_pct(vals),
                        sharpe=sharpe_ratio(vals),
                    )
                    # 基准归因（成立以来窗口 + 近期窗口都算，日期对齐）
                    fv, bv = align_by_dates(sub, bench_series)
                    if len(fv) >= 40:
                        m.beta, m.alpha_annual_pct, m.info_ratio = beta_alpha_ir(fv, bv)
                    item.windows.append(m)
            items.append(item)

        report = CompareReport(baseline=baseline, items=items)
        report.summary_md = self._summary_md(report)
        return report

    async def _scale_inst_map(self, fund_ids: list[int]) -> dict[int, dict]:
        """库内季报 → {fund_id: {growth 首末规模比, institution 最新机构占比%}}"""
        rows = (await self.db.execute(
            select(FundQuarterly)
            .where(
                FundQuarterly.fund_id.in_(fund_ids),
                FundQuarterly.fund_size.is_not(None),
            )
            .order_by(FundQuarterly.fund_id, FundQuarterly.report_date)
        )).scalars().all()

        out: dict[int, dict] = {}
        by_fund: dict[int, list[FundQuarterly]] = {}
        inst_latest: dict[int, tuple[str, float]] = {}
        for r in rows:
            by_fund.setdefault(r.fund_id, []).append(r)
            if r.institution_holding_ratio is not None:
                rd = r.report_date or ""
                cur = inst_latest.get(r.fund_id)
                if cur is None or rd >= cur[0]:
                    inst_latest[r.fund_id] = (rd, float(r.institution_holding_ratio))

        for fid, quarters in by_fund.items():
            sizes = [q.fund_size for q in quarters if q.fund_size and q.fund_size > 0]
            growth = round(sizes[-1] / sizes[0], 2) if len(sizes) >= 2 and sizes[0] > 0 else None
            out[fid] = {
                "growth": growth,
                "institution": inst_latest.get(fid, (None, None))[1],
            }
        return out

    @staticmethod
    def _summary_md(r: CompareReport) -> str:
        lines = [
            "## 📊 基金 PK 对比报告",
            "",
            f"> 口径：净值统一计算（无风险利率 {RF_ANNUAL}%）；基准 {r.baseline}。仅供参考，不构成投资建议。",
            "",
            "| 基金 | 窗口 | 年化收益 | 最大回撤 | 夏普 | Beta | Alpha年化 | 信息比率 | 规模变化 | 机构占比 |",
            "|------|------|----------|----------|------|------|-----------|----------|----------|----------|",
        ]
        for it in r.items:
            if it.error:
                lines.append(f"| {it.fund_name}({it.fund_code}) | 失败: {it.error} | - | - | - | - | - | - | - | - |")
                continue
            inst = f"{it.institution_pct:.1f}%" if it.institution_pct is not None else "—"
            grow = f"{it.scale_growth}×" if it.scale_growth else "—"
            for w in it.windows:
                lines.append(
                    f"| {it.fund_name}({it.fund_code}) | {w.window_label} "
                    f"| {w.annual_return_pct if w.annual_return_pct is not None else '—'}% "
                    f"| {w.max_drawdown_pct if w.max_drawdown_pct is not None else '—'}% "
                    f"| {w.sharpe if w.sharpe is not None else '—'} "
                    f"| {w.beta if w.beta is not None else '—'} "
                    f"| {w.alpha_annual_pct if w.alpha_annual_pct is not None else '—'}% "
                    f"| {w.info_ratio if w.info_ratio is not None else '—'} "
                    f"| {grow} | {inst} |"
                )
        return "\n".join(lines)
