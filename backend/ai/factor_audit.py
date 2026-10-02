"""因子与策略效果诊断引擎（纯 Python，确定性、可测试、零 token 成本）

方法论（场外基金业界通行评估，映射到本系统已落库数据）：
- 因子层：逐日截面 RankIC / IC（vs T+h 前瞻净值收益）、IR = IC均值/IC标准差、覆盖度
  · Q12（2026-10-02）：horizon=h 的 T+h 前瞻收益在相邻交易日重叠 h-1 天，按每日计数会把
    IR 放大 √h 倍。因此 IC 序列默认**每 h 个交易日取一个非重叠样本**（`overlapping_ic=True`
    回退旧行为），并额外给出 `ic_n_eff`（独立周期数）、`rank_ic_ir_annualized`、
    双尾 p 值与同窗口多因子的 Benjamini–Hochberg q 值 —— 诊断结论必须带这三个口径。
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
from backend.utils.timezone import beijing_today

logger = logging.getLogger(__name__)

MIN_CROSS_SECTION = 5      # 与截面标准化同口径：有效样本 <5 的交易日不参与 IC
MIN_IC_PERIODS_FOR_CONCLUSION = 8   # Q12：非重叠周期少于此数 → 只出过程不出"哪个因子更好"的结论
TRADING_DAYS_PER_YEAR = 252
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


def _betacf(a: float, b: float, x: float, itmax: int = 300, eps: float = 3e-12) -> float:
    """不完全贝塔函数的连分式（Lentz 算法），供 t 分布双尾 p 值用"""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """正则化不完全贝塔 I_x(a,b)"""
    import math
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                + a * math.log(x) + b * math.log(1.0 - x))
    front = math.exp(ln_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t_stat: float, df: int) -> Optional[float]:
    """Student-t 双尾 p 值：p = I_{df/(df+t²)}(df/2, 1/2)；df<=0 时无定义"""
    import math
    if df <= 0 or not math.isfinite(t_stat):
        return None
    x = df / (df + float(t_stat) * float(t_stat))
    return max(0.0, min(1.0, _betainc(df / 2.0, 0.5, x)))


def ic_p_value(ic_series: list[float]) -> Optional[float]:
    """IC 序列均值 ≠ 0 的双尾 p 值（单样本 t 检验）；n<2 或方差为 0 → None"""
    import math
    n = len(ic_series)
    if n < 2:
        return None
    arr = np.asarray(ic_series, float)
    sd = float(arr.std(ddof=1))
    if sd <= 0:
        return None
    return t_two_sided_p(float(arr.mean()) / (sd / math.sqrt(n)), n - 1)


def benjamini_hochberg(pvals: list[Optional[float]]) -> list[Optional[float]]:
    """同窗口多因子的多重比较校正（BH step-up，返回 q 值，单调且不超 1）

    11 个因子在同一个 horizon 上各算一个 p，取其中"最大"的那个接近取噪声极值 ——
    不校正就等于默许多重比较。None（样本不足）原样保留，不参与排名。
    """
    idx = [i for i, p in enumerate(pvals) if p is not None]
    out: list[Optional[float]] = [None] * len(pvals)
    m = len(idx)
    if m == 0:
        return out
    order = sorted(idx, key=lambda i: pvals[i])          # 升序
    prev = 1.0
    for rank, i in enumerate(reversed(order), start=1):  # 从最大 p 往回，保证单调
        k = m - rank + 1                                 # 该 p 的升序名次
        q = min(prev, pvals[i] * m / k)
        out[i] = round(min(1.0, q), 6)
        prev = out[i]
    return out


@dataclass
class FactorIcStats:
    factor: str
    horizon: int
    ic_mean: Optional[float] = None
    rank_ic_mean: Optional[float] = None
    rank_ic_ir: Optional[float] = None       # rank_ic 均值 / 标准差（按**非重叠周期**计）
    rank_ic_positive_ratio: Optional[float] = None
    days: int = 0                            # 实际用于均值/IR 的周期数（非重叠 = 独立周期数）
    avg_pairs: float = 0.0                   # 日均有效样本
    # ── Q12 口径三件套 ──
    n_days_valid: int = 0                    # 满足最小截面的交易日数（重叠口径下的原始长度）
    sampling: str = "non_overlapping"        # non_overlapping | overlapping（回退开关）
    rank_ic_ir_annualized: Optional[float] = None
    rank_ic_p_value: Optional[float] = None  # 双尾 t 检验：IC 均值 ≠ 0
    rank_ic_q_bh: Optional[float] = None     # 同窗口全因子 BH 校正后的 q 值（由 audit 回填）
    significant: Optional[bool] = None       # q < 0.05；None = 样本或方差不足以检验

    def to_dict(self) -> dict:
        return {
            "factor": self.factor, "horizon": self.horizon,
            "ic_mean": _r4(self.ic_mean), "rank_ic_mean": _r4(self.rank_ic_mean),
            "rank_ic_ir": _r4(self.rank_ic_ir),
            "rank_ic_positive_ratio": _r4(self.rank_ic_positive_ratio),
            "days": self.days, "avg_pairs": round(self.avg_pairs, 1),
            "n_days_valid": self.n_days_valid, "sampling": self.sampling,
            "rank_ic_ir_annualized": _r4(self.rank_ic_ir_annualized),
            "rank_ic_p_value": _r4(self.rank_ic_p_value),
            "rank_ic_q_bh": _r4(self.rank_ic_q_bh),
            "significant": self.significant,
        }


def _r4(v) -> Optional[float]:
    return None if v is None else round(float(v), 4)


def daily_ic(samples_by_date: dict[str, dict[str, list[tuple[float, float]]]],
             factor: str, horizon: int, overlapping: bool = False) -> FactorIcStats:
    """samples_by_date: {date: {factor: [(score, fwd_return), ...]}}（已过滤 None 与不足样本）

    Q12：T+h 的前瞻收益在相邻交易日重叠 h-1 天，逐日算 IC 会让序列强自相关、IR 虚高 √h 倍。
    默认每 `horizon` 个有效交易日取一个样本（非重叠）；`overlapping=True` 回退旧的逐日口径。
    """
    st = FactorIcStats(
        factor=factor, horizon=horizon,
        sampling="overlapping" if overlapping else "non_overlapping",
    )
    ics, rics = [], []
    pair_counts = []
    for d in sorted(samples_by_date):        # 日期升序：非重叠抽样必须是等距时间抽样
        pairs = samples_by_date[d].get(factor)
        if not pairs or len(pairs) < MIN_CROSS_SECTION:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        ic, ric = pearson(xs, ys), spearman(xs, ys)
        if ic is not None and ric is not None:
            ics.append(ic)
            rics.append(ric)
            pair_counts.append(len(pairs))
    st.n_days_valid = len(rics)
    if not overlapping and horizon > 1 and rics:
        step = int(horizon)
        keep = range(0, len(rics), step)
        ics = [ics[i] for i in keep]
        rics = [rics[i] for i in keep]
        pair_counts = [pair_counts[i] for i in keep]
    st.days = len(rics)
    if rics:
        ra = np.asarray(rics)
        ia = np.asarray(ics)
        st.ic_mean = float(ia.mean())
        st.rank_ic_mean = float(ra.mean())
        if ra.std() > 0:
            st.rank_ic_ir = float(ra.mean() / ra.std())
            # 一个周期 = horizon 个交易日 → 年化倍率 √(252/horizon)
            st.rank_ic_ir_annualized = st.rank_ic_ir * float(np.sqrt(TRADING_DAYS_PER_YEAR / horizon))
        st.rank_ic_positive_ratio = float((ra > 0).mean())
        st.avg_pairs = float(np.mean(pair_counts))
        st.rank_ic_p_value = ic_p_value([float(v) for v in rics])
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
    ic_sampling: str = "non_overlapping"                     # Q12：IC 序列采样口径
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def summary_md(self) -> str:
        lines = [
            f"# 因子与策略诊断（{self.window_start} ~ {self.window_end}）",
            f"样本：{self.rows_total} 行 / {self.funds_total} 只（净值可用 {self.funds_with_nav} 只）",
            "",
            "## 因子 RankIC（截面，vs 前瞻净值收益）",
        ]
        # Q12：先说清 IR 的分母，否则"IR=1.2"会被当成可下结论的强度
        periods = {}
        for f in self.factor_ic:
            periods.setdefault(f["horizon"], []).append(f.get("days") or 0)
        caliber = "，".join(
            f"T+{h} 的 IR 基于 {max(v)} 个{'非重叠' if self.ic_sampling == 'non_overlapping' else '逐日重叠'}周期"
            for h, v in sorted(periods.items())
        )
        lines.append(
            f"> {caliber or '无非重叠周期'}；p 值为 IC 均值≠0 的双尾 t 检验，"
            "q 为同窗口全因子 Benjamini–Hochberg 校正后的假发现率（q<0.05 才标 ✓）。"
        )
        lines += [
            "| 因子 | 窗口(日) | RankIC均值 | IR | IR年化 | 独立周期 | 为正占比 | p | q(BH) | 显著 | 日均样本 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for f in sorted(self.factor_ic, key=lambda x: -(x["rank_ic_mean"] if x["rank_ic_mean"] is not None else -9)):
            sig = "—" if f.get("significant") is None else ("✓" if f.get("significant") else "✗")
            lines.append(
                f"| {f['factor']} | {f['horizon']} | {f['rank_ic_mean']} | {f['rank_ic_ir']} "
                f"| {f.get('rank_ic_ir_annualized')} | {f.get('days')} | {f['rank_ic_positive_ratio']} "
                f"| {f.get('rank_ic_p_value')} | {f.get('rank_ic_q_bh')} | {sig} | {f['avg_pairs']} |"
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
        overlapping_ic: bool = False,
    ) -> FactorAuditReport:
        """overlapping_ic=True 回退 Q12 之前的逐日重叠 IC 口径（仅作对照/回滚用）"""
        start = beijing_today() - timedelta(days=int(days))
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
            window_start=str(start), window_end=str(beijing_today()),
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
            stats = [daily_ic(samples[h], f, h, overlapping=overlapping_ic) for f in factors]
            # 同窗口 m 个因子各出一个 p 值 → 不校正等于默许多重比较
            for st, q in zip(stats, benjamini_hochberg([s.rank_ic_p_value for s in stats])):
                st.rank_ic_q_bh = q
                st.significant = None if q is None else bool(q < 0.05)
            report.factor_ic += [st.to_dict() for st in stats]
            n_eff = max((s.days for s in stats), default=0)
            report.ic_sampling = ("overlapping" if overlapping_ic else "non_overlapping")
            if stats and n_eff < MIN_IC_PERIODS_FOR_CONCLUSION:
                report.caveats.append(
                    f"horizon={h} 在 {days} 天窗口内只有 {n_eff} 个非重叠周期"
                    f"（< {MIN_IC_PERIODS_FOR_CONCLUSION}）：RankIC 排序接近噪声，"
                    "不要据此调整因子权重；如需结论请把 days 拉长到 ≥ 3×horizon"
                )
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
