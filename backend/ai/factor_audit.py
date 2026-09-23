"""因子与策略效果诊断引擎（纯 Python，确定性、可测试、零 token 成本）

方法论（场外基金业界通行评估，映射到本系统已落库数据）：
- 因子层：逐日截面 RankIC / IC（vs T+h 前瞻净值收益）、IR = IC均值/IC标准差、覆盖度
- 评分层：全池按 weighted_score 五分位的前瞻收益单调性（评分是否真的排序有效）
- 信号层：buy/sell 相对同日池均值的超额胜率、平均超额、翻转(whipsaw)次数
- 质量层：original_score vs weighted_score 修正差；有/无 quality_warnings 两组后续收益差
  （三字段 2026-09-23 起落库，旧行 NULL 自动跳过并计入 coverage 说明）

LLM 只消费本模块的结构化输出做解读，不参与任何数值计算。
"""

import bisect
import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Awaitable, Callable, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund

logger = logging.getLogger(__name__)

MIN_CROSS_SECTION = 5      # 与截面标准化同口径：有效样本 <5 的交易日不参与 IC
NavSeries = list[tuple[str, float]]  # [(YYYY-MM-DD, nav)] 升序

# 注入式净值提供者：async (code, period_days) -> Optional[NavSeries]
NavProvider = Callable[[str, int], Awaitable[Optional[NavSeries]]]


# ═══════════════════════════════════════════════════════════════════
# 1. 纯统计函数（无 DB / 无网络，黄金用例直接测它们）
# ═══════════════════════════════════════════════════════════════════

def forward_return(nav: Optional[NavSeries], on_date: str, horizon: int) -> Optional[float]:
    """on_date 当日或之前最近净值日往后 horizon 个净值的收益；越界返回 None"""
    if not nav:
        return None
    dates = [d for d, _ in nav]
    idx = bisect.bisect_right(dates, on_date) - 1
    if idx < 0 or idx + horizon >= len(nav):
        return None
    base, fwd = nav[idx][1], nav[idx + horizon][1]
    if base <= 0:
        return None
    return fwd / base - 1.0


def _rank(values: np.ndarray) -> np.ndarray:
    """平均并列名次（Spearman 所需）"""
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1, dtype=float)
    # 并列取均值：按值分组
    sorted_v = values[order]
    i = 0
    while i < len(sorted_v):
        j = i
        while j + 1 < len(sorted_v) and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        if j > i:
            grp = ranks[order[i:j + 1]]
            ranks[order[i:j + 1]] = grp.mean()
        i = j + 1
    return ranks


def pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    xa, ya = np.asarray(xs, float), np.asarray(ys, float)
    if xa.std() == 0 or ya.std() == 0:
        return None
    return float(np.corrcoef(xa, ya)[0, 1])


def spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return pearson(list(_rank(np.asarray(xs, float))), list(_rank(np.asarray(ys, float))))


@dataclass
class FactorIcStats:
    factor: str
    horizon: int
    ic_mean: Optional[float] = None
    rank_ic_mean: Optional[float] = None
    rank_ic_ir: Optional[float] = None       # rank_ic 均值 / 标准差
    rank_ic_positive_ratio: Optional[float] = None
    days: int = 0                            # 参与计算的有效交易日数
    avg_pairs: float = 0.0                   # 日均有效样本

    def to_dict(self) -> dict:
        return {
            "factor": self.factor, "horizon": self.horizon,
            "ic_mean": _r4(self.ic_mean), "rank_ic_mean": _r4(self.rank_ic_mean),
            "rank_ic_ir": _r4(self.rank_ic_ir),
            "rank_ic_positive_ratio": _r4(self.rank_ic_positive_ratio),
            "days": self.days, "avg_pairs": round(self.avg_pairs, 1),
        }


def _r4(v) -> Optional[float]:
    return None if v is None else round(float(v), 4)


def daily_ic(samples_by_date: dict[str, dict[str, tuple[float, float]]],
             factor: str, horizon: int) -> FactorIcStats:
    """samples_by_date: {date: {factor: [(score, fwd_return), ...]}}（已过滤 None 与不足样本）"""
    st = FactorIcStats(factor=factor, horizon=horizon)
    ics, rics = [], []
    pair_counts = []
    for _, fmap in samples_by_date.items():
        pairs = fmap.get(factor)
        if not pairs or len(pairs) < MIN_CROSS_SECTION:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        ic, ric = pearson(xs, ys), spearman(xs, ys)
        if ic is not None and ric is not None:
            ics.append(ic)
            rics.append(ric)
            pair_counts.append(len(pairs))
    st.days = len(rics)
    if rics:
        ra = np.asarray(rics)
        ia = np.asarray(ics)
        st.ic_mean = float(ia.mean())
        st.rank_ic_mean = float(ra.mean())
        st.rank_ic_ir = float(ra.mean() / ra.std()) if ra.std() > 0 else None
        st.rank_ic_positive_ratio = float((ra > 0).mean())
        st.avg_pairs = float(np.mean(pair_counts))
    return st


def quintile_returns(score_fwd: list[tuple[float, float]], buckets: int = 5) -> list[dict]:
    """按评分升序分位 → 每组平均前瞻收益（单调性=评分有效性）"""
    if len(score_fwd) < buckets * 2:
        return []
    arr = sorted(score_fwd, key=lambda x: x[0])
    n = len(arr)
    out = []
    for b in range(buckets):
        lo, hi = n * b // buckets, n * (b + 1) // buckets
        grp = [f for _, f in arr[lo:hi]]
        out.append({
            "quintile": b + 1, "count": len(grp),
            "score_range": [round(arr[lo][0], 2), round(arr[hi - 1][0], 2)],
            "avg_fwd_return": round(float(np.mean(grp)) * 100, 3),
        })
    return out


def signal_stats(rows: list[dict]) -> list[dict]:
    """rows: {direction, fwd, pool_avg} → 按方向统计相对池均超额胜率。
    buy 赢 = fwd > pool_avg；sell 赢 = fwd < pool_avg；hold 不参与。"""
    stat: dict[str, list] = {}
    for r in rows:
        d = r.get("direction")
        if d in ("buy", "sell") and r.get("fwd") is not None and r.get("pool_avg") is not None:
            excess = r["fwd"] - r["pool_avg"]
            stat.setdefault(d, []).append(excess if d == "buy" else -excess)
    return [
        {
            "direction": d, "count": len(v),
            "win_rate": round(float(np.mean(np.asarray(v) > 0)), 4),
            "avg_excess_pct": round(float(np.mean(v)) * 100, 3),
        }
        for d, v in sorted(stat.items())
    ]


def whipsaw_counts(code_dir_series: dict[str, list[tuple[str, str]]]) -> list[dict]:
    """方向翻转次数（忽略 hold，比较相邻两个非 hold 信号是否反向）"""
    out = []
    for code, seq in code_dir_series.items():
        flips = 0
        last = None
        for _, direction in seq:
            if direction not in ("buy", "sell"):
                continue
            if last is not None and direction != last:
                flips += 1
            last = direction
        if flips:
            out.append({"code": code, "flips": flips, "observations": len(seq)})
    return sorted(out, key=lambda x: -x["flips"])


def group_compare(with_val: list[float], without_val: list[float]) -> dict:
    """警告组 vs 无警告组平均前瞻收益差"""
    return {
        "with_n": len(with_val), "without_n": len(without_val),
        "with_avg_pct": round(float(np.mean(with_val)) * 100, 3) if with_val else None,
        "without_avg_pct": round(float(np.mean(without_val)) * 100, 3) if without_val else None,
        "delta_pct": round((float(np.mean(with_val)) - float(np.mean(without_val))) * 100, 3)
        if with_val and without_val else None,
    }


# ═══════════════════════════════════════════════════════════════════
# 2. 装载与分析（依赖 DB + 注入的净值提供者）
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FactorAuditReport:
    window_start: str
    window_end: str
    horizons: list[int]
    rows_total: int
    funds_total: int
    funds_with_nav: int = 0
    factor_ic: list[dict] = field(default_factory=list)
    score_quintiles: dict = field(default_factory=dict)      # horizon → buckets
    signal_win_rate: list[dict] = field(default_factory=list)
    whipsaw_top: list[dict] = field(default_factory=list)
    quality_group_compare: Optional[dict] = None             # 警告组对照
    correction_effect: Optional[dict] = None                 # original→final 修正统计
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def summary_md(self) -> str:
        lines = [
            f"# 因子与策略诊断（{self.window_start} ~ {self.window_end}）",
            f"样本：{self.rows_total} 行 / {self.funds_total} 只（净值可用 {self.funds_with_nav} 只）",
            "",
            "## 因子 RankIC（截面，vs 前瞻净值收益）",
            "| 因子 | 窗口(日) | RankIC均值 | IR | 为正占比 | 有效天数 | 日均样本 |",
            "|---|---|---|---|---|---|---|",
        ]
        for f in sorted(self.factor_ic, key=lambda x: -(x["rank_ic_mean"] if x["rank_ic_mean"] is not None else -9)):
            lines.append(
                f"| {f['factor']} | {f['horizon']} | {f['rank_ic_mean']} | {f['rank_ic_ir']} "
                f"| {f['rank_ic_positive_ratio']} | {f['days']} | {f['avg_pairs']} |"
            )
        for h, buckets in self.score_quintiles.items():
            lines += [f"", f"## 评分五分位前瞻收益（T+{h}，%）",
                      "| 分位 | 评分区间 | 只次 | 平均收益% |", "|---|---|---|---|"]
            lines += [f"| Q{b['quintile']} | {b['score_range'][0]}~{b['score_range'][1]} "
                      f"| {b['count']} | {b['avg_fwd_return']} |" for b in buckets]
        if self.signal_win_rate:
            lines += ["", "## 信号相对池均超额胜率", "| 方向 | 次数 | 胜率 | 平均超额% |", "|---|---|---|---|"]
            lines += [f"| {s['direction']} | {s['count']} | {round(s['win_rate'] * 100, 1)}% "
                      f"| {s['avg_excess_pct']} |" for s in self.signal_win_rate]
        if self.caveats:
            lines += ["", "## 数据口径提示"] + [f"- {c}" for c in self.caveats]
        return "\n".join(lines)


async def _default_nav_provider(code: str, period_days: int) -> Optional[NavSeries]:
    """默认净值源：复用分析适配器（走既有防封节奏与 f10/lsjz 降级链）"""
    from backend.data_sources.akshare_adapter import AKShareAdapter
    try:
        fd = await AKShareAdapter().get_fund_data(code, period=period_days)
    except Exception as e:
        logger.warning(f"诊断净值拉取失败 {code}: {type(e).__name__}")
        return None
    if not fd or not fd.close_history or not fd.date_history:
        return None
    series: NavSeries = []
    for d, nav in zip(fd.date_history, fd.close_history):
        key = str(d)[:10]
        try:
            series.append((key, float(nav)))
        except (TypeError, ValueError):
            continue
    series.sort()
    return series or None


class FactorAuditService:
    def __init__(self, db: AsyncSession, nav_provider: Optional[NavProvider] = None) -> None:
        self.db = db
        self._nav = nav_provider or _default_nav_provider

    async def audit(
        self,
        days: int = 90,
        horizons: tuple[int, ...] = (5, 20),
        fund_codes: Optional[list[str]] = None,
    ) -> FactorAuditReport:
        start = date.today() - timedelta(days=int(days))
        stmt = (
            select(AnalysisResult, Fund.code)
            .join(Fund, AnalysisResult.fund_id == Fund.id)
            .where(AnalysisResult.analysis_date >= start)
            .order_by(AnalysisResult.analysis_date)
        )
        if fund_codes:
            stmt = stmt.where(Fund.code.in_([c.strip() for c in fund_codes]))
        rows = (await self.db.execute(stmt)).all()

        report = FactorAuditReport(
            window_start=str(start), window_end=str(date.today()),
            horizons=list(horizons), rows_total=len(rows),
            funds_total=len({code for _, code in rows}),
        )
        if not rows:
            report.caveats.append("窗口内无分析记录")
            return report

        # 1. 净值装载（周期 = 窗口天数 + 最大持有期 + 缓冲，上限 500 防超长拉取）
        nav_period = min(days + max(horizons) * 2 + 40, 500)
        nav_map: dict[str, Optional[NavSeries]] = {}
        codes = sorted({code for _, code in rows})
        for c in codes:
            nav_map[c] = await self._nav(c, nav_period)
        report.funds_with_nav = sum(1 for v in nav_map.values() if v)
        if report.funds_with_nav == 0:
            report.caveats.append("全部基金净值不可用（疑似数据源封禁），仅输出信号分布类统计")
        if report.funds_with_nav < report.funds_total:
            missing = [c for c, v in nav_map.items() if not v]
            report.caveats.append(f"净值缺失 {len(missing)} 只: {','.join(missing[:10])}")

        # 2. 逐行展开：因子分/前瞻收益/池均/警告
        # samples[date][factor] = [(score, fwd)]（各 horizon 分开）
        samples: dict[int, dict] = {h: {} for h in horizons}
        score_fwd: dict[int, list] = {h: [] for h in horizons}
        day_returns: dict[int, dict[str, list[float]]] = {h: {} for h in horizons}
        dir_rows: dict[int, list[dict]] = {h: [] for h in horizons}
        warn_vals: dict[int, tuple[list, list]] = {h: ([], []) for h in horizons}
        corrections = []
        code_series: dict[str, list] = {}

        for r, code in rows:
            d = str(r.analysis_date)
            code_series.setdefault(code, []).append((d, r.signal_direction))
            if r.original_score is not None:
                corrections.append(r.weighted_score - r.original_score)
            scores = _parse_scores(r.factor_scores)
            for h in horizons:
                fwd = forward_return(nav_map.get(code), d, h)
                if fwd is None:
                    continue
                day_returns[h].setdefault(d, []).append(fwd)
                score_fwd[h].append((r.weighted_score, fwd))
                pool = None  # 池均稍后统一回填
                dir_rows[h].append({"direction": r.signal_direction, "fwd": fwd,
                                    "date": d, "code": code})
                if r.quality_warnings is not None or (r.original_score is not None):
                    has_warn = bool(json.loads(r.quality_warnings) if isinstance(r.quality_warnings, str)
                                    else r.quality_warnings)
                    warn_vals[h][0 if has_warn else 1].append(fwd)
                bucket = samples[h].setdefault(d, {})
                for f, sc in scores.items():
                    bucket.setdefault(f, []).append((sc, fwd))

        # 3. 池均值回填 + 指标计算
        for h in horizons:
            day_avg = {d: float(np.mean(v)) for d, v in day_returns[h].items()}
            for row in dir_rows[h]:
                row["pool_avg"] = day_avg.get(row["date"])
            report.signal_win_rate += [
                dict(s, horizon=h) for s in signal_stats(dir_rows[h])
            ]
            report.score_quintiles[str(h)] = quintile_returns(score_fwd[h])
            factors = sorted({f for fmap in samples[h].values() for f in fmap})
            for f in factors:
                report.factor_ic.append(daily_ic(samples[h], f, h).to_dict())
            if warn_vals[h][0] and warn_vals[h][1] and h == horizons[0]:
                report.quality_group_compare = {
                    "horizon": h, **group_compare(warn_vals[h][0], warn_vals[h][1]),
                }

        report.whipsaw_top = whipsaw_counts(code_series)[:10]
        if corrections:
            ca = np.asarray(corrections)
            report.correction_effect = {
                "count": len(ca),
                "avg_shift": _r4(float(ca.mean())),
                "abs_shift_gt_05_ratio": _r4(float((np.abs(ca) > 0.5).mean())),
            }
        n_rows_with_warnings = sum(
            1 for r, _ in rows
            if r.quality_warnings
        )
        if n_rows_with_warnings < 10:
            report.caveats.append(
                f"quality_warnings 落库行数仅 {n_rows_with_warnings}（2026-09-23 起新增），"
                "质量过滤有效性统计需再积累样本"
            )
        return report


def _parse_scores(raw) -> dict[str, float]:
    try:
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    out = {}
    for f, v in (d or {}).items():
        sc = v.get("score") if isinstance(v, dict) else v
        try:
            out[f] = float(sc)
        except (TypeError, ValueError):
            continue
    return out
