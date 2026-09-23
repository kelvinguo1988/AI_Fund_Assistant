"""调仓分析引擎（纯 Python，确定性）— 对当前持仓/基金池产出四清单 + 组合层约束

口径与信号链路完全一致：
- 阈值取每只基金最新一期落库的 dynamic_buy/sell_threshold（缺失回退 ±1.5 基础阈值）
- 买入可执行性复用场外申购状态（暂停申购/封闭期 → 移入 buy_blocked，与
  apply_otc_trade_constraint 同一规则），状态缺失时保留并标注未知
- 翻转(whipsaw)复用 factor_audit.whipsaw_counts（只比较相邻非 hold 信号）
- 无真实持仓时按「基金池等权近似」出候选（用户既定设计①）
LLM 只解读本模块输出，不做任何数值计算。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.ai.factor_audit import whipsaw_counts
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.models.fund_holding import FundHolding
from backend.models.user_position import UserPosition

logger = logging.getLogger(__name__)

BASE_BUY_TH = 1.5
BASE_SELL_TH = -1.5
WATCH_BAND = 0.5          # 阈值边界带（观望确认区）
DETERIORATION_DELTA = -1.0  # 窗口内因子分总和下降≥1 个桶位视为恶化
OVERLAP_TWIN = 5          # 持仓两两重仓重合 ≥5 只 → 双胞胎警示
BOUNDARY_WEIGHT_PCT = 30.0  # 单一赛道权重 ≥30% → 集中度警示

BUY_BAN_STATUS = ("暂停申购", "封闭期")


def _scores_sum(raw) -> float:
    try:
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return 0.0
    total = 0.0
    for val in (d or {}).values():
        s = val.get("score") if isinstance(val, dict) else val
        try:
            total += float(s or 0)
        except (TypeError, ValueError):
            pass
    return total


def _safe_warnings(raw) -> list:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _top_theme(exposure_tags: Optional[str]) -> str:
    """exposure_tags 首桶赛道名：'半导体×3 45.2%, 军工×1 8% (覆盖53%)' → 半导体"""
    if not exposure_tags:
        return ""
    part = exposure_tags.split("(")[0].split(",")[0].strip()
    return part.split("×")[0].strip()


def _is_qdii(fund: Fund) -> bool:
    blob = f"{fund.name} {fund.tags or ''} {fund.fund_type_official or ''}".upper()
    return "QDII" in blob


@dataclass
class RebalanceReport:
    as_of: str = ""
    window_days: int = 30
    holdings_mode: str = "positions"       # positions / pool_equal_weight_approx
    holdings: list = field(default_factory=list)
    sells: list = field(default_factory=list)
    buys: list = field(default_factory=list)
    buy_blocked: list = field(default_factory=list)
    swaps: list = field(default_factory=list)
    watch: list = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    caveats: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of, "window_days": self.window_days,
            "holdings_mode": self.holdings_mode,
            "holdings": self.holdings, "sells": self.sells, "buys": self.buys,
            "buy_blocked": self.buy_blocked, "swaps": self.swaps, "watch": self.watch,
            "constraints": self.constraints, "caveats": self.caveats,
        }

    def summary_md(self) -> str:
        lines = [
            f"# 调仓工单（{self.as_of}，窗口 {self.window_days} 天）",
            f"持仓口径：{'真实持仓（user_positions）' if self.holdings_mode == 'positions' else '未录入持仓 → 基金池等权近似'}",
        ]
        mode_note = "" if self.holdings_mode == "positions" else "（等权近似，权重仅示意）"
        if self.sells:
            lines.append(f"\n## 减仓/卖出候选{mode_note}")
            for s in self.sells:
                lines.append(f"- **{s['name']}**({s['code']}) 评分 {s['score']}：{'；'.join(s['reasons'])}")
        if self.buys:
            lines.append(f"\n## 买入候选")
            for b in self.buys:
                note = f"（{b['overlap_note']}）" if b.get("overlap_note") else ""
                lines.append(f"- **{b['name']}**({b['code']}) 评分 {b['score']} 申购状态：{b.get('otc_status', '未知')}{note}")
        if self.buy_blocked:
            lines.append("\n## 买入被否决（不可申购）")
            for b in self.buy_blocked:
                lines.append(f"- {b['name']}({b['code']}) 评分 {b['score']}：{b['reason']}")
        if self.swaps:
            lines.append(f"\n## 同赛道换仓配对（按分差排序）")
            for p in self.swaps:
                lines.append(
                    f"- {p['sell_name']}({p['sell_code']}) → {p['buy_name']}({p['buy_code']})："
                    f"赛道「{p['theme']}」分差 {p['score_gap']}"
                )
        if self.watch:
            lines.append(f"\n## 观望/待确认")
            for w in self.watch:
                lines.append(f"- {w['name']}({w['code']}) 评分 {w['score']}：{w['reason']}（建议确认 {w['confirm_days']} 日）")
        c = self.constraints
        if c.get("theme_concentration"):
            lines.append("\n## 组合层约束")
            for t in c["theme_concentration"]:
                lines.append(f"- 赛道集中度：「{t['theme']}」权重 {t['weight_pct']}%（≥{BOUNDARY_WEIGHT_PCT:.0f}% 阈值）")
        if c.get("qdii_weight_pct") is not None:
            lines.append(f"- QDII 权重：{c['qdii_weight_pct']}%")
        for pair in c.get("twin_pairs", []):
            lines.append(f"- 重仓重合：{pair['a_name']} × {pair['b_name']} 共 {pair['common']} 只（≥{OVERLAP_TWIN} 双胞胎警示）")
        for v in self.caveats:
            lines.append(f"\n> ⚠ {v}")
        return "\n".join(lines)


class RebalanceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def analyze(
        self,
        window_days: int = 30,
        otc_status_map: Optional[dict[str, dict]] = None,
        top_n: int = 8,
    ) -> RebalanceReport:
        window_days = max(min(int(window_days), 250), 7)
        report = RebalanceReport(window_days=window_days)
        db = self.db

        funds = (await db.execute(
            select(Fund).where(Fund.status == "active").order_by(Fund.code)
        )).scalars().all()
        if not funds:
            report.caveats.append("基金池为空，无法调仓分析")
            return report
        fund_by_id = {f.id: f for f in funds}

        # ── 每只基金最新一期信号 + 窗口内序列（恶化/翻转）──
        since = date.today() - timedelta(days=window_days)
        rows = (await db.execute(
            select(AnalysisResult, Fund.id.label("fid"))
            .join(Fund, AnalysisResult.fund_id == Fund.id)
            .where(Fund.status == "active", AnalysisResult.analysis_date >= since)
            .order_by(AnalysisResult.analysis_date)
        )).all()
        latest: dict[int, AnalysisResult] = {}
        series: dict[str, list] = {}
        first_sum: dict[int, float] = {}
        for r, _fid in rows:
            fid = r.fund_id
            latest[fid] = r  # 按日期升序遍历，最后写入即最新
            first_sum.setdefault(fid, _scores_sum(r.factor_scores))
            code = fund_by_id.get(fid)
            if code:
                series.setdefault(code.code, []).append((str(r.analysis_date), r.signal_direction))
        report.as_of = str(max((r.analysis_date for r in latest.values()), default=date.today()))
        if not latest:
            report.caveats.append(f"窗口 {window_days} 天内无分析记录，请先执行分析")
            return report

        # ── 持仓与权重（无持仓 → 池等权近似）──
        pos_rows = (await db.execute(
            select(UserPosition, Fund).join(Fund, UserPosition.fund_id == Fund.id)
        )).all()
        positions = {p.fund_id: p for p, _f in pos_rows}
        if positions:
            report.holdings_mode = "positions"
            weights = {}
            for fid, p in positions.items():
                weights[fid] = p.shares * p.cost_nav if p.cost_nav else 1.0
            total_w = sum(weights.values()) or 1.0
            weights = {fid: w / total_w for fid, w in weights.items()}
            if all(p.cost_nav is None for p in positions.values()) and len(positions) > 1:
                report.caveats.append("持仓未填成本价，权重按等权近似")
        else:
            report.holdings_mode = "pool_equal_weight_approx"
            weights = {f.id: 1.0 / len(funds) for f in funds}
            report.caveats.append("未录入真实持仓，权重按基金池等权近似，卖出/观望针对全池候选")

        # ── 申购状态（默认取类缓存；conftest 屏蔽时为空 → 标注未知）──
        if otc_status_map is None:
            try:
                from backend.services.index_valuation_service import OtcTradeStatusService
                otc_status_map = await OtcTradeStatusService.get_status_map()
            except Exception as e:
                logger.warning(f"调仓分析取申购状态失败: {e}")
                otc_status_map = {}
        if not otc_status_map:
            report.caveats.append("申购状态数据不可用：买入候选未做可执行性否决（建议部署环境重试或人工核查）")

        # ── 重仓重合数据（最新季报，池内一次取全）──
        holdings_map = await self._fund_stock_sets(list(fund_by_id))
        flips_by_code = {x["code"]: x["flips"] for x in whipsaw_counts(series)}

        held_ids = set(positions) if report.holdings_mode == "positions" else set(fund_by_id)
        # 买入排除集：真实持仓模式排除已持有基金；等权近似模式允许高分标的作为换仓去向
        buy_excluded = set(positions) if report.holdings_mode == "positions" else set()

        # ── 四清单分类 ──
        for fid, r in latest.items():
            f = fund_by_id.get(fid)
            if f is None:
                continue
            buy_th = r.dynamic_buy_threshold if r.dynamic_buy_threshold is not None else BASE_BUY_TH
            sell_th = r.dynamic_sell_threshold if r.dynamic_sell_threshold is not None else BASE_SELL_TH
            warnings = _safe_warnings(r.quality_warnings)
            delta = _scores_sum(r.factor_scores) - first_sum.get(fid, 0.0)
            deteriorated = delta <= DETERIORATION_DELTA
            base = {
                "code": f.code, "name": f.name, "score": r.weighted_score,
                "direction": r.signal_direction,
                "theme": _top_theme(f.exposure_tags),
                "weight_pct": round(weights.get(fid, 0.0) * 100, 1),
            }
            held = fid in held_ids

            # 卖出/减仓：持仓内、低分且有恶化佐证；低分无佐证 → 观望
            if held and (r.weighted_score <= sell_th or r.signal_direction == "sell"):
                reasons = [f"评分 {r.weighted_score} ≤ 卖出阈值 {sell_th}" if r.weighted_score <= sell_th
                          else f"信号已翻空（阈值 {sell_th}）"]
                aggravators = []
                if deteriorated:
                    aggravators.append(f"窗口内因子分总和恶化 {delta:.1f}")
                if warnings:
                    aggravators.append(f"质量警告：{'、'.join(warnings[:3])}")
                if flips_by_code.get(f.code, 0) >= 2:
                    aggravators.append(f"近{window_days}天翻转 {flips_by_code[f.code]} 次")
                if aggravators:
                    report.sells.append({**base, "sell_threshold": sell_th,
                                         "reasons": reasons + aggravators})
                else:
                    report.watch.append({**base, "reason": f"低分（≤{sell_th}）但无恶化佐证",
                                         "confirm_days": 3})
                continue

            # 买入：未持有（真实持仓模式）的高分候选 + 可执行性；
            # 等权近似模式下高分池内基金也作为换仓去向保留
            if fid not in buy_excluded and (r.weighted_score >= buy_th or r.signal_direction == "buy"):
                status = (otc_status_map or {}).get(f.code) or {}
                purchase = str(status.get("purchase") or "").strip()
                item = {**base, "buy_threshold": buy_th,
                        "otc_status": purchase or ("场内/未知" if f.fund_type != "otc" else "未知")}
                if purchase in BUY_BAN_STATUS:
                    report.buy_blocked.append({**base, "reason": f"场外{purchase}，买入不可执行"})
                    continue
                common = self._max_overlap_with_holdings(f, held_ids, holdings_map)
                if common is not None:
                    item["overlap_note"] = f"与持仓重仓重合 {common} 只"
                if deteriorated:
                    item["note"] = f"因子分窗口内回落 {delta:.1f}，谨慎"
                report.buys.append(item)
                continue

            # 观望：阈值边界带 / 翻转高发 / 恶化但方向未翻
            near_buy = abs(r.weighted_score - buy_th) <= WATCH_BAND
            near_sell = abs(r.weighted_score - sell_th) <= WATCH_BAND
            flips = flips_by_code.get(f.code, 0)
            if near_buy or near_sell or flips >= 2:
                why = []
                if near_buy:
                    why.append(f"贴近买入阈值 {buy_th}")
                if near_sell:
                    why.append(f"贴近卖出阈值 {sell_th}")
                if flips >= 2:
                    why.append(f"近{window_days}天翻转 {flips} 次")
                report.watch.append({**base, "reason": "、".join(why),
                                     "confirm_days": 3 if flips >= 2 else 2})

        report.sells.sort(key=lambda x: x["score"])
        report.buys.sort(key=lambda x: -x["score"])
        report.buys = report.buys[:top_n]
        report.swaps = self._pair_swaps(report.sells, report.buys)
        report.holdings = [
            {
                "code": fund_by_id[fid].code, "name": fund_by_id[fid].name,
                "weight_pct": round(weights.get(fid, 0.0) * 100, 1),
                "score": latest[fid].weighted_score if fid in latest else None,
                "direction": latest[fid].signal_direction if fid in latest else None,
                "theme": _top_theme(fund_by_id[fid].exposure_tags),
                "qdii": _is_qdii(fund_by_id[fid]),
            }
            for fid in held_ids if fid in fund_by_id
        ]
        report.constraints = await self._portfolio_constraints(held_ids, weights, fund_by_id, holdings_map)
        return report

    async def _fund_stock_sets(self, fund_ids: list[int]) -> dict[int, set[str]]:
        """各基金最新季前十大重仓股票代码集合"""
        fh = aliased(FundHolding)
        latest_q = (
            select(func.max(fh.quarter_label))
            .where(fh.fund_id == FundHolding.fund_id)
            .correlate(FundHolding).scalar_subquery()
        )
        rows = (await self.db.execute(
            select(FundHolding.fund_id, FundHolding.stock_code)
            .where(FundHolding.fund_id.in_(fund_ids), FundHolding.quarter_label == latest_q)
        )).all()
        out: dict[int, set[str]] = {}
        for fid, sc in rows:
            out.setdefault(fid, set()).add(sc)
        return out

    @staticmethod
    def _max_overlap_with_holdings(fund: Fund, held_ids: set[int],
                                   holdings_map: dict[int, set[str]]) -> Optional[int]:
        mine = holdings_map.get(fund.id)
        if not mine:
            return None
        best = 0
        found = False
        for fid in held_ids:
            other = holdings_map.get(fid)
            if fid == fund.id or not other:
                continue
            found = True
            best = max(best, len(mine & other))
        # 重合 ≤2 属正常分散，不打扰；≥3 才提示换入等于变相加仓
        return best if found and best >= 3 else None

    @staticmethod
    def _pair_swaps(sells: list, buys: list, max_pairs: int = 5) -> list:
        """同赛道 卖×买 按分差重排"""
        pairs = []
        for s in sells:
            for b in buys:
                if s["theme"] and s["theme"] == b["theme"]:
                    pairs.append({
                        "sell_code": s["code"], "sell_name": s["name"],
                        "buy_code": b["code"], "buy_name": b["name"],
                        "theme": s["theme"],
                        "score_gap": round(b["score"] - s["score"], 2),
                    })
        pairs.sort(key=lambda p: -p["score_gap"])
        return pairs[:max_pairs]

    @staticmethod
    async def _portfolio_constraints(held_ids, weights, fund_by_id, holdings_map) -> dict:
        theme_w: dict[str, float] = {}
        qdii_w = 0.0
        for fid in held_ids:
            f = fund_by_id.get(fid)
            if not f:
                continue
            w = weights.get(fid, 0.0)
            theme = _top_theme(f.exposure_tags) or "未归类"
            theme_w[theme] = theme_w.get(theme, 0.0) + w
            if _is_qdii(f):
                qdii_w += w
        concentration = [
            {"theme": t, "weight_pct": round(w * 100, 1)}
            for t, w in sorted(theme_w.items(), key=lambda x: -x[1])
            if w * 100 >= BOUNDARY_WEIGHT_PCT and t != "未归类"
        ]
        twins = []
        ids = [fid for fid in held_ids if holdings_map.get(fid)]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                common = len(holdings_map[ids[i]] & holdings_map[ids[j]])
                if common >= OVERLAP_TWIN:
                    twins.append({
                        "a_code": fund_by_id[ids[i]].code, "a_name": fund_by_id[ids[i]].name,
                        "b_code": fund_by_id[ids[j]].code, "b_name": fund_by_id[ids[j]].name,
                        "common": common,
                    })
        twins.sort(key=lambda x: -x["common"])
        return {
            "theme_concentration": concentration,
            "qdii_weight_pct": round(qdii_w * 100, 1),
            "twin_pairs": twins[:10],
        }
