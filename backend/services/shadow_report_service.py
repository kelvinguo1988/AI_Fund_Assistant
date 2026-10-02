"""影子口径分歧日报（§3 第 2/3 条）— 把"新口径改了会怎样"变成一个可判读的数字

生产信号仍是旧口径，新口径只落在 `analysis_results.shadow_*`。本服务只读这些列做
汇总，给出 §3 写死的切换判据进度：**最近连续 STABLE_DAYS_REQUIRED 个交易日的合并分歧
样本，其分歧比例的 Wilson 单侧 95% 置信上界 < DIVERGENCE_THRESHOLD_PCT**
（2026-10-02 从"逐日比例都 <15% 且连续 5 日"改过来，理由见 `docs/QUANT_DECISIONS_2026-10.md` §3）。

为什么不是逐日比例：NAS 实测每日有对照的行只有 14 行 ⇒ 单日分歧率的最小刻度是 7.1%，
2 只 14.3% 贴线过、3 只 21.4% 一票否决，判据实际在测"当天有几只踩在阈值 ±0.5 的边际带上"
（这批行占 21%），而不是在测两口径是否等价。合并窗口 + 置信上界同时解决两件事：
单日翻转不再靠运气否决，样本不够时上界必然压不进 15%（n=14 即使 0 分歧上界也 16.2%），
薄池**从公式上就无法下结论**，不需要再加"池子小于多少不算"的特判。

四条诚实性约束（比报表本身重要）：
1. **只数有影子对照的行**。`shadow_*` 为 NULL = 该行没跑影子（开关关、变体未注册、
   被前置否决），不能当成"新口径也同意"，否则分歧比例会被系统性稀释。
2. **判据二不自动判**。"影子口径在 Q6 新基线上的超额不劣于旧口径"要把影子信号喂进
   回测/复盘，而回测跑的是生产列（旧口径）—— 影子口径的历史收益本层给不出，
   报表里明写"需人工"，不编数。
3. **变体切换会打断可比性**。窗口内出现多个 `shadow_variant` 时逐日标注变体，
   汇总数字按变体分开看才有效。
4. **判据一的"连续 5 个交易日"必须按 A 股交易日历数**（见 `_criterion_window`）：
   周末/节假日手点一轮也会落一行 `analysis_date=当天`，若只按"有影子数据的日期"倒序取窗口，
   5 天可以全是周六跑的，判据一就能被"手痒"刷满。休市轮次仍在日表里展示（标
   `trading_day=False`），但不进窗口；中间漏掉一个真实交易日同样打断连续 ——
   改的是"哪天算达标"，不是"哪天算样本"，这条必须守住。

零上游请求：纯本地 SQL + Python 汇总；交易日判定读库内 `holiday_calendar`（与 Q9 同一张表）。
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_sources.trading_calendar import load_off_day_dates
from backend.engines.shadow_scoring import (
    DIRECTION_SKIP, DIVERGENCE_THRESHOLD_PCT, STABLE_DAYS_REQUIRED, load_shadow_config,
)
from backend.models.analysis_result import AnalysisResult

logger = logging.getLogger(__name__)

MAX_WINDOW_DAYS = 60
# 单日样本低于此数时，分歧比例的随机波动就足以越过 15% 判据线，单独标注
LOW_SAMPLE_ROWS = 10
BIG_DELTA = 0.5
# 回溯"上一个交易日"的安全上限：国庆+中秋连休也远在 40 天内，超出即视为日历异常
MAX_TRADING_BACKTRACK = 40
# Wilson 区间的 z 值：判据一只关心"真实分歧率最多是多少"，取单侧 95%（非双侧的 1.96）
WILSON_Z_ONE_SIDED_95 = 1.645
# 分母小到零分歧都压不进判据线时 `max_tolerated_divergences` 的返回值（区别于"容忍 0 次"）
TOLERANCE_IMPOSSIBLE = -1


def is_a_share_trading_day(d: date, off_days: frozenset) -> bool:
    """A 股交易日（与 `trading_calendar.is_a_share_trading_day_async` 同口径）

    周末一律休市（**调休补班的周六股市也不开市**），`off_days` 里的法定节假日剔除。
    """
    return d.weekday() < 5 and d not in off_days


def prev_trading_day(d: date, off_days: frozenset) -> Optional[date]:
    """`d` 之前的上一个 A 股交易日（含日历回溯上限保护）"""
    cur = d - timedelta(days=1)
    for _ in range(MAX_TRADING_BACKTRACK):
        if is_a_share_trading_day(cur, off_days):
            return cur
        cur -= timedelta(days=1)
    return None


def wilson_upper_bound(divergent: int, rows: int,
                       z: float = WILSON_Z_ONE_SIDED_95) -> Optional[float]:
    """分歧比例的 Wilson 单侧 95% 置信上界（百分点；无样本返回 None）

    判据一在 14 只/日的薄池上要回答的是"真实分歧率**最多**是多少"，不是点估：
    n=70 时 10 次分歧点估 14.3% 看着过线，上界却是 22.5%；反过来 n=14 即使 0 次分歧
    上界也有 16.2% —— 样本不够就永远压不进 15%，这正是薄池该有的行为。
    """
    if rows <= 0:
        return None
    p = divergent / rows
    denom = 1 + z * z / rows
    center = p + z * z / (2 * rows)
    margin = z * math.sqrt(p * (1 - p) / rows + z * z / (4 * rows * rows))
    return (center + margin) / denom * 100


def max_tolerated_divergences(rows: int) -> int:
    """这个分母能压进判据线的最多分歧只数（n=70 → 5 只，第 6 只上界 15.7% 出线）

    **连零分歧都压不进线时返回 -1**（`TOLERANCE_IMPOSSIBLE`）而不是 0：n≤15 时 0 分歧的
    上界本身就 ≥15%，报成"容忍 0 次"会被读成"零分歧就能通过"，而它恰恰永远不可能通过。
    """
    if rows <= 0 or (wilson_upper_bound(0, rows) or 100.0) >= DIVERGENCE_THRESHOLD_PCT:
        return TOLERANCE_IMPOSSIBLE
    k = 0
    while k < rows and (wilson_upper_bound(k + 1, rows) or 100.0) < DIVERGENCE_THRESHOLD_PCT:
        k += 1
    return k


def tolerance_note(rows: int) -> str:
    """把"这个分母能容忍几次分歧"说成一句独立可读的话

    caveat、结论文案和前端 tooltip 共用一份，免得 -1 这个哨兵值在三处各自翻译一遍。
    """
    tol = max_tolerated_divergences(rows)
    if tol < 0:
        return (f"当前 {rows} 只次的窗口即使零分歧，95% 上界也有 "
                f"{(wilson_upper_bound(0, rows) or 0):.1f}% ≥ {DIVERGENCE_THRESHOLD_PCT}%，"
                "判据一在这个样本量下不可能满足")
    return f"当前 {rows} 只次的窗口最多容忍 {tol} 次分歧，再多一次置信上界就越线"


def _direction_pair(old: str, new: str) -> str:
    return f"{old}->{new}"


def _bucket_of(original_score: Optional[float],
               buy_th: Optional[float], sell_th: Optional[float]) -> str:
    """按当次动态阈值分档（§3：按 original_score / dynamic_buy_threshold 分档看迁移）

    用的是**修正前**原始分：新口径改了权重与阈值，若用修正后分档，档位本身就随口径漂移，
    看不出"同一批分数处在什么位置"。阈值缺失的旧行归 unknown，不参与分档结论。
    """
    if original_score is None or buy_th is None or sell_th is None:
        return "unknown"
    if original_score >= buy_th:
        return "above_buy"
    if original_score <= sell_th:
        return "below_sell"
    return "middle"


BUCKET_LABELS = {
    "above_buy": "原始分已达买入线",
    "middle": "原始分在买卖线之间",
    "below_sell": "原始分已破卖出线",
    "unknown": "阈值/原始分缺失（旧行）",
}


class ShadowReportService:
    """影子口径分歧报表"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build(self, days: int = 10) -> dict[str, Any]:
        """近 `days` 个有影子数据的日期的分歧情况；**判据窗口只认 A 股交易日**"""
        try:
            window = int(days)
        except (TypeError, ValueError):
            window = 10
        window = max(1, min(window, MAX_WINDOW_DAYS))

        cfg = await load_shadow_config(self.db)
        dated_rows, dates, window_truncated = await self._load(window)

        out: dict[str, Any] = {
            "window_days": window,
            "divergence_threshold_pct": DIVERGENCE_THRESHOLD_PCT,
            "stable_days_required": STABLE_DAYS_REQUIRED,
            "shadow_enabled": cfg["enabled"],
            "active_variant": cfg["variant"],
            "registered_variants": cfg["registered"],
            "daily": [],
            "migration": {},
            "buckets": {},
            "variants": {},
            "summary": {},
            "caveats": [],
            "meets_ratio_criterion": False,
            "conclusion": "",
        }

        caveats: list[str] = []
        out["caveats"] = caveats

        if not dated_rows:
            caveats.extend(self._no_data_reasons(cfg))
            out["conclusion"] = "尚无影子对照数据"
            return out

        # ── 逐日汇总（时间正序，便于前端画趋势）──
        # 交易日判定一次读库内日历（与 Q9 同表，零上游请求），窗口两端各留 45 天余量，
        # 保证跨长假回溯"上一个交易日"时日历不缺。
        span_start = min(dates) - timedelta(days=45)
        off_days = await load_off_day_dates(self.db, span_start, max(dates))

        daily: list[dict[str, Any]] = []
        for d in sorted(dated_rows):
            item = self._day_stats(d, dated_rows[d])
            item["trading_day"] = is_a_share_trading_day(d, off_days)
            daily.append(item)
        out["daily"] = daily

        run, skipped_non_trading = self._criterion_window(daily, off_days)
        out["summary"] = self._aggregate(daily)
        out["summary"].update(self._criterion_stats(run[-STABLE_DAYS_REQUIRED:], len(run)))
        out["summary"]["non_trading_rounds"] = skipped_non_trading
        out["meets_ratio_criterion"] = (
            out["summary"]["criterion_window_complete"]
            and out["summary"]["criterion_upper_pct"] is not None
            and out["summary"]["criterion_upper_pct"] < DIVERGENCE_THRESHOLD_PCT
        )

        # 分档迁移（全窗口）与变体分布
        out["buckets"] = self._bucket_stats(dated_rows)
        out["variants"] = self._variant_stats(dated_rows)

        caveats.extend(self._interpret_caveats(daily, out["summary"], out["variants"]))
        if skipped_non_trading:
            dates_str = ", ".join(x["date"] for x in daily if not x["trading_day"])[:60]
            caveats.append(
                f"{skipped_non_trading} 个轮次落在「非交易日」（周末/法定节假日，含调休补班周六）："
                f"这些天股市没有新净值，那一轮的对照与上一个交易日完全同质，已从判据一的"
                f"连续交易日中剔除（{dates_str}）"
            )
        if run and window_truncated and self._run_hit_window_edge(run, daily):
            caveats.append(
                f"连续有对照的交易日已顶到窗口边界（days={window}）：真实连续天数可能更大，"
                "加大 days 再确认（判据一只看最后 "
                f"{STABLE_DAYS_REQUIRED} 个交易日，这条是提示窗口取小了）"
            )
        out["conclusion"] = self._conclusion(out["summary"], out["meets_ratio_criterion"])
        return out

    # ── 判据一的样本窗口（Q12 同批的口径诚实性延伸：尺子自己得先对）──

    @staticmethod
    def _criterion_window(daily: list[dict], off_days: frozenset) -> tuple[list[dict], int]:
        """末尾**连续有影子对照的 A 股交易日**序列（时间正序，长度即攒到的天数）

        返回 `(连续日列表, 被剔除的非交易日轮次数)`。打断连续的只有一件事：那个交易日
        **根本没跑影子**（漏跑一天就重新攒，否则"周五 + 下周一"也能被读成"连续两天"）。
        单日分歧率 ≥ 15% **不再**把那天剔出窗口 —— 判据一比的是窗口合并样本的置信上界，
        单日翻转是样本的一部分，把它从分母里挖掉等于偷看答案。
        """
        non_trading = sum(1 for x in daily if not x["trading_day"])
        by_date = {
            x["date"]: x for x in daily if x["trading_day"] and x["shadow_rows"]
        }
        if not by_date:
            return [], non_trading

        last = max(by_date)
        cursor = date.fromisoformat(last)
        run = [by_date[last]]
        for _ in range(MAX_WINDOW_DAYS):
            prev = prev_trading_day(cursor, off_days)
            if prev is None or prev.isoformat() not in by_date:
                break
            run.append(by_date[prev.isoformat()])
            cursor = prev
        run.reverse()
        return run, non_trading

    @staticmethod
    def _criterion_stats(window: list[dict], consecutive_days: int) -> dict[str, Any]:
        """把判据一的窗口压成一组可判读数字（分母、分歧数、点估、单侧 95% 上界、容忍度）"""
        rows = sum(x["shadow_rows"] for x in window)
        divergent = sum(x["divergent"] for x in window)
        raw_upper = wilson_upper_bound(divergent, rows) if rows else None
        return {
            "criterion_days": len(window),
            "criterion_dates": [x["date"] for x in window],
            "criterion_rows": rows,
            "criterion_divergent": divergent,
            "criterion_pct": round(divergent / rows * 100, 1) if rows else None,
            "criterion_upper_pct": round(raw_upper, 1) if raw_upper is not None else None,
            "consecutive_days": consecutive_days,
            "criterion_window_complete": consecutive_days >= STABLE_DAYS_REQUIRED,
            # 当前分母下能通过判据的最多分歧只数：让"再翻转几只就出线"变成一个可读的数
            # （-1 = 零分歧也压不进线，此时用下面的 note 说话，别把 -1 直接印给用户）
            "criterion_tolerance": max_tolerated_divergences(rows),
            "criterion_tolerance_note": tolerance_note(rows),
        }

    @staticmethod
    def _run_hit_window_edge(run: list[dict], daily: list[dict]) -> bool:
        """连续日是否一直铺到窗口里最早的那一天（说明真实连续天数可能被 days 截断）"""
        loaded = [x["date"] for x in daily if x["trading_day"] and x["shadow_rows"]]
        return bool(run) and bool(loaded) and run[0]["date"] == min(loaded)

    # ── 取数 ──

    async def _load(self, window: int) -> tuple[dict[date, list[dict]], list[date], bool]:
        """取最近 `window` 个有影子行的日期及其行（一次日期查询 + 一次行查询）

        多取一个日期只为判断"窗口是否真的截断了数据"（`truncated`）：判据一的连续天数
        顶到窗口最早那天时，分不清"真的只连续这么多"还是"被 days 砍掉了"，必须如实提示。
        """
        date_stmt = (
            select(AnalysisResult.analysis_date)
            .where(AnalysisResult.shadow_direction.isnot(None))
            .group_by(AnalysisResult.analysis_date)
            .order_by(AnalysisResult.analysis_date.desc())
            .limit(window + 1)
        )
        found = [r[0] for r in (await self.db.execute(date_stmt)).all()]
        truncated = len(found) > window
        dates = found[:window]
        if not dates:
            return {}, [], truncated

        row_stmt = select(
            AnalysisResult.analysis_date,
            AnalysisResult.signal_direction,
            AnalysisResult.weighted_score,
            AnalysisResult.shadow_direction,
            AnalysisResult.shadow_score,
            AnalysisResult.shadow_variant,
            AnalysisResult.original_score,
            AnalysisResult.dynamic_buy_threshold,
            AnalysisResult.dynamic_sell_threshold,
            AnalysisResult.pool_size,
            AnalysisResult.factor_coverage,
        ).where(AnalysisResult.analysis_date.in_(dates))
        rows = (await self.db.execute(row_stmt)).all()

        grouped: dict[date, list[dict]] = {}
        for r in rows:
            grouped.setdefault(r.analysis_date, []).append({
                "old_dir": r.signal_direction,
                "new_dir": r.shadow_direction,
                "old_score": r.weighted_score,
                "new_score": r.shadow_score,
                "variant": r.shadow_variant or "",
                "original_score": r.original_score,
                "buy_th": r.dynamic_buy_threshold,
                "sell_th": r.dynamic_sell_threshold,
                "pool_size": r.pool_size,
                "coverage": r.factor_coverage,
            })
        return grouped, dates, truncated

    # ── 统计 ──

    @staticmethod
    def _day_stats(d: date, rows: list[dict]) -> dict[str, Any]:
        # 只数真正有影子对照的行：shadow_direction 为 NULL 的行（当日未跑影子）不参与
        shadowed = [r for r in rows if r["new_dir"]]
        if not shadowed:
            return {
                "date": d.isoformat(), "rows": len(rows), "shadow_rows": 0,
                "divergent": 0, "divergence_pct": 0.0, "avg_delta": None,
                "max_abs_delta": None, "big_delta": 0, "migration": {},
                "variants": [], "pool_size": None, "coverage": None,
                "skip_rows": 0, "low_sample": True,
            }
        divergent = [r for r in shadowed if r["old_dir"] != r["new_dir"]]
        skip_rows = sum(1 for r in shadowed if r["new_dir"] == DIRECTION_SKIP)
        deltas = [round(r["new_score"] - r["old_score"], 4)
                  for r in shadowed if r["new_score"] is not None and r["old_score"] is not None]
        migration: dict[str, int] = {}
        for r in divergent:
            key = _direction_pair(r["old_dir"], r["new_dir"])
            migration[key] = migration.get(key, 0) + 1
        pools = [r["pool_size"] for r in shadowed if r["pool_size"]]
        covs = [r["coverage"] for r in shadowed if r["coverage"] is not None]
        return {
            "date": d.isoformat(),
            "rows": len(rows),
            "shadow_rows": len(shadowed),
            "divergent": len(divergent),
            "divergence_pct": round(len(divergent) / len(shadowed) * 100, 1),
            "avg_delta": round(sum(deltas) / len(deltas), 3) if deltas else None,
            "max_abs_delta": round(max(abs(x) for x in deltas), 3) if deltas else None,
            "big_delta": sum(1 for x in deltas if abs(x) >= BIG_DELTA),
            "migration": migration,
            "variants": sorted({r["variant"] for r in shadowed if r["variant"]}),
            "pool_size": max(pools) if pools else None,
            "coverage": round(sum(covs) / len(covs), 3) if covs else None,
            "skip_rows": skip_rows,
            "low_sample": len(shadowed) < LOW_SAMPLE_ROWS,
        }

    @staticmethod
    def _aggregate(daily: list[dict]) -> dict[str, Any]:
        shadow_rows = sum(x["shadow_rows"] for x in daily)
        divergent = sum(x["divergent"] for x in daily)
        migration: dict[str, int] = {}
        for x in daily:
            for k, v in x["migration"].items():
                migration[k] = migration.get(k, 0) + v
        buy_days = [x for x in daily if x["shadow_rows"]]
        return {
            "days_with_shadow": len(buy_days),
            "shadow_rows": shadow_rows,
            "divergent": divergent,
            "divergence_pct": round(divergent / shadow_rows * 100, 1) if shadow_rows else 0.0,
            "avg_divergence_pct": round(
                sum(x["divergence_pct"] for x in buy_days) / len(buy_days), 1
            ) if buy_days else 0.0,
            "migration": dict(sorted(migration.items(), key=lambda kv: -kv[1])),
            "buy_to_other": sum(v for k, v in migration.items() if k.startswith("buy->")),
            "sell_to_other": sum(v for k, v in migration.items() if k.startswith("sell->")),
            "skip_rows": sum(x.get("skip_rows", 0) for x in daily),
        }

    @staticmethod
    def _bucket_stats(dated_rows: dict[date, list[dict]]) -> dict[str, Any]:
        """按当次动态阈值分档看方向迁移（§3 第 2 条的"分布迁移"）"""
        acc: dict[str, dict[str, Any]] = {}
        for rows in dated_rows.values():
            for r in rows:
                if not r["new_dir"]:
                    continue
                b = _bucket_of(r["original_score"], r["buy_th"], r["sell_th"])
                slot = acc.setdefault(b, {
                    "label": BUCKET_LABELS[b], "rows": 0, "divergent": 0,
                    "avg_delta": 0.0, "_deltas": [], "migration": {},
                })
                slot["rows"] += 1
                if r["old_dir"] != r["new_dir"]:
                    slot["divergent"] += 1
                    key = _direction_pair(r["old_dir"], r["new_dir"])
                    slot["migration"][key] = slot["migration"].get(key, 0) + 1
                if r["new_score"] is not None and r["old_score"] is not None:
                    slot["_deltas"].append(r["new_score"] - r["old_score"])
        out: dict[str, Any] = {}
        for b, slot in acc.items():
            deltas = slot.pop("_deltas")
            slot["avg_delta"] = round(sum(deltas) / len(deltas), 3) if deltas else None
            slot["divergence_pct"] = round(slot["divergent"] / slot["rows"] * 100, 1) if slot["rows"] else 0.0
            out[b] = slot
        # 稳定顺序：达线 → 中间 → 破线 → 缺失
        order = ["above_buy", "middle", "below_sell", "unknown"]
        return {k: out[k] for k in order if k in out}

    @staticmethod
    def _variant_stats(dated_rows: dict[date, list[dict]]) -> dict[str, Any]:
        acc: dict[str, dict[str, Any]] = {}
        for d, rows in dated_rows.items():
            for r in rows:
                if not r["new_dir"]:
                    continue
                name = r["variant"] or "(未标记变体)"
                slot = acc.setdefault(name, {"rows": 0, "dates": set()})
                slot["rows"] += 1
                slot["dates"].add(d)
        return {
            name: {
                "rows": s["rows"],
                "first_date": min(s["dates"]).isoformat(),
                "last_date": max(s["dates"]).isoformat(),
            }
            for name, s in sorted(acc.items(), key=lambda kv: -kv[1]["rows"])
        }

    # ── 解读文案 ──

    @staticmethod
    def _no_data_reasons(cfg: dict) -> list[str]:
        reasons = []
        if not cfg["enabled"]:
            reasons.append(
                f"影子评分开关已关闭（{DIVERGENCE_THRESHOLD_PCT}% 判据无数据可算）；"
                "PUT /api/analysis/shadow-config 里 enabled=1 后重跑分析"
            )
        if not cfg["registered"]:
            reasons.append(
                "注册表里没有任何影子变体 —— 内置的 2C 新口径（Q2/Q3/Q4/Q1）没被注册进来。"
                "正常运行时分析服务会导入 `engines/shadow_variants` 完成注册，"
                "这里为空说明那条导入链没走到（进程刚起还没跑过分析，或导入失败）；"
                "注册表为空时每轮分析都不产生 shadow_* 行，这不是故障而是「当前没有可对比的口径」"
            )
        else:
            reasons.append(
                f"已注册变体：{cfg['registered']}；影子列为空说明还没有跑过一轮带影子的分析"
                "（影子只在新的分析轮次产生，历史行不回填）"
            )
        return reasons

    @staticmethod
    def _interpret_caveats(daily: list[dict], summary: dict,
                           variants: dict) -> list[str]:
        caveats: list[str] = []
        # 判据口径本身也要交底：读数字的人得知道"单日翻转不再一票否决"是他放弃的权利
        caveats.append(
            f"判据一口径（2026-10-02 起）：连续 {STABLE_DAYS_REQUIRED} 个交易日的「合并」分歧率"
            f"单侧 95% Wilson 置信上界 < {DIVERGENCE_THRESHOLD_PCT}%，"
            f"不是「每个单日都 <{DIVERGENCE_THRESHOLD_PCT}%」——单日翻转不再一票否决；"
            f"{summary.get('criterion_tolerance_note', '')}；"
            "样本不够时上界必然压不进线，薄池下不出结论"
        )
        low = [x["date"] for x in daily if x["shadow_rows"] and x["low_sample"]]
        if low:
            caveats.append(
                f"{len(low)} 个轮次的影子样本 < {LOW_SAMPLE_ROWS} 只"
                f"（{', '.join(low[:3])}{'...' if len(low) > 3 else ''}）："
                "这些天只按行数进入判据一的合并分母（样本少的日子贡献就少），"
                "单日比例本身不再单独决定通过/否决"
            )
        if len(variants) > 1:
            caveats.append(
                "窗口内出现多个影子变体（" + "、".join(variants) + "）："
                "变体切换日起的数字与之前不可比，判据一的窗口必须整体落在同一个变体内才有效"
            )
        pools = [x["pool_size"] for x in daily if x["pool_size"]]
        if pools and max(pools) < 20:
            caveats.append(
                f"截面样本仅 {max(pools)} 只：截面标准化/分位数本身不稳定，"
                "两口径的差异里有多少来自池子大小无法区分（Q5）"
            )
        covs = [x["coverage"] for x in daily if x["coverage"] is not None]
        if covs and min(covs) < 0.85:
            caveats.append(
                f"最低因子覆盖率 {round(min(covs) * 100)}%（<85%）：数据缺失因子占权重，"
                "两口径的归一方式对这块处理不同，分歧里混了覆盖率噪声（Q4）"
            )
        if summary.get("skip_rows"):
            caveats.append(
                f"其中 {summary['skip_rows']} 行是「{DIRECTION_SKIP}」：新口径认为覆盖率不足、"
                "本轮不出记录。这类分歧的含义是「记录消失」（仪表盘/推送/回测里都不会再有这只），"
                "比买卖翻转更重，判据一把它算作分歧是保守做法"
            )
        caveats.append(
            "判据二「影子口径在 Q6 新基线（vs 静态 50%）上的超额不劣于旧口径」本表不自动判定："
            "回测/复盘读的是生产列（旧口径），影子口径的历史收益需要人工另跑一套对比，"
            "报表里不给编出来的数字"
        )
        if summary["shadow_rows"] and not summary["divergent"]:
            caveats.append("窗口内零分歧：要么两口径确实一致，要么变体是空实现 —— 先看变体明细")
        return caveats

    @staticmethod
    def _conclusion(summary: dict, met: bool) -> str:
        dates = summary.get("criterion_dates") or []
        span = f"{dates[0]}~{dates[-1]}" if dates else "（无对照日）"
        if met:
            return (
                f"判据一已满足：{span} 共 {summary['criterion_days']} 个连续交易日、"
                f"{summary['criterion_rows']} 只次对照里 {summary['criterion_divergent']} 次分歧"
                f"（点估 {summary['criterion_pct']}%，单侧 95% 上界 {summary['criterion_upper_pct']}%，"
                f"< {DIVERGENCE_THRESHOLD_PCT}%；{summary['criterion_tolerance_note']}）。"
                "可以准备切换到新口径"
                "（判据二仍需人工：影子口径的超额本表给不出）"
            )
        if not summary.get("criterion_window_complete"):
            return (
                f"判据一未满足：末尾连续有影子对照的交易日只有 {summary.get('consecutive_days', 0)} 个"
                f"（判据要 {STABLE_DAYS_REQUIRED} 个，漏跑一个交易日就重新攒），窗口不整不下结论"
            )
        return (
            f"判据一未满足：{span} 的 {summary['criterion_rows']} 只次对照里 "
            f"{summary['criterion_divergent']} 次分歧，点估 {summary['criterion_pct']}% 但"
            f"单侧 95% 置信上界 {summary['criterion_upper_pct']}% ≥ {DIVERGENCE_THRESHOLD_PCT}%"
            f"（{summary['criterion_tolerance_note']}）。要么两口径真的更一致，"
            "要么把截面样本做大 —— 加大 days 不会让这个上界变小"
        )


async def get_divergence_report(db: AsyncSession, days: int = 10) -> dict[str, Any]:
    """端点入口：影子口径分歧报表"""
    return await ShadowReportService(db).build(days=days)
