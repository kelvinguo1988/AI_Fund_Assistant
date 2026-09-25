"""组合 X 光透视服务 — 持仓穿透 / 重叠矩阵 / 经理公司集中度 / 多样化评分

借鉴 fundadvisor portfolio_xray 的可解释扣分制评分模型（2026-09-24 用户确认
做成系统原生 Skill），数据全部来自库内：
- 持仓：fund_holdings 最新季报 top10（穿透到个股）
- 经理：fund_manager_records（经理集中度——单点依赖告警）
- 季度规模：fund_quarterly（公司集中度数据源之一）

评分（可解释扣分制，100 起步）:
  -hhi_pen   个股集中度：可见口径 HHI，0.02(≈50 只等效)免罚，
             0.10(≈10 只等效)罚满 35 分
  -ovl_pen   基金间重叠：两两 Jaccard 均值 ×60，封顶 20 分；
             任一对共同重仓 ≥5 只给去重建议
  -mgr_pen   经理单点：某经理 ≥50% 仓位罚 15（≥35% 罚 8）
  -comp_pen  公司单点：某公司 ≥60% 仓位罚 10
  -count_pen 单基金组合罚 20
"""

import logging
import math
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund import Fund
from backend.models.fund_holding import FundHolding
from backend.models.fund_manager_record import FundManagerRecord
from backend.models.fund_quarterly import FundQuarterly

logger = logging.getLogger(__name__)

# 2026-09-24 校准：穿透只覆盖季报 top10（组合的 30~70%），未披露部分
# 天然摊薄 HHI——实测正常分散组合穿透 HHI 落在 0.03~0.06。
# 免罚线 0.06（≈17 只等效持股），满罚 0.15（≈7 只等效，真实抱团）
HHI_FREE = 0.06
HHI_FULL_PEN = 0.15
HHI_PEN_MAX = 35.0
OVL_PEN_MAX = 20.0
MGR_MAJOR = 0.50
MGR_MINOR = 0.35
COMP_MAJOR = 0.60
SINGLE_FUND_PEN = 20.0


async def _latest_quarter_holdings(
    db: AsyncSession, fund_ids: list[int]
) -> dict[int, list[FundHolding]]:
    """各基金最新季度持仓（含占比>0 的全量行，穿透按权重加权）"""
    if not fund_ids:
        return {}
    rows = (await db.execute(
        select(FundHolding)
        .where(FundHolding.fund_id.in_(fund_ids))
        .order_by(FundHolding.fund_id, FundHolding.quarter_label.desc())
    )).scalars().all()
    latest_q: dict[int, str] = {}
    for h in rows:
        latest_q.setdefault(h.fund_id, h.quarter_label)
    out: dict[int, list[FundHolding]] = {}
    for h in rows:
        if latest_q.get(h.fund_id) == h.quarter_label and (h.ratio or 0) > 0:
            out.setdefault(h.fund_id, []).append(h)
    return out


def _hhi(weighted: dict[str, float]) -> float:
    """赫芬达尔指数（合计占比归一到 1 的口径）"""
    total = sum(weighted.values())
    if total <= 0:
        return 0.0
    return sum((v / total) ** 2 for v in weighted.values())


def xray_score(funds: list[dict]) -> dict:
    """纯函数：对已构造的基金持仓集算 X 光评分（可单测）

    Args:
        funds: [{code, name, manager, company,
                 holdings: [(stock_code, stock_name, weight 占净值%)]}]
    """
    n = len(funds)
    warnings: list[str] = []
    suggestions: list[str] = []
    score = 100.0

    # ── 1. 个股穿透（按基金权重加权到组合层）──
    fund_w_total = sum(f.get("weight", 1.0) for f in funds) or 1.0
    stock_weighted: dict[str, float] = defaultdict(float)
    stock_names: dict[str, str] = {}
    matched: list[dict] = []
    for f in funds:
        w = f.get("weight", 1.0) / fund_w_total
        stocks = [(c, nm, r) for c, nm, r in f.get("holdings", []) if r]
        if stocks:
            matched.append({
                "code": f["code"], "name": f["name"],
                "manager": f.get("manager") or "", "company": f.get("company") or "",
                "w": w, "stocks": stocks,
            })
        for c, nm, r in stocks:
            stock_weighted[c] += w * r
            stock_names.setdefault(c, nm)

    top_stocks = sorted(
        (
            {"code": c, "name": stock_names.get(c, c),
             "portfolio_pct": round(v, 2)}
            for c, v in stock_weighted.items()
        ),
        key=lambda x: -x["portfolio_pct"],
    )[:15]
    hhi = _hhi(stock_weighted)
    effective_count = int(round(1 / hhi)) if hhi > 0 else 0

    hhi_pen = min(HHI_PEN_MAX, max(0.0, (hhi - HHI_FREE) / (HHI_FULL_PEN - HHI_FREE) * HHI_PEN_MAX))
    score -= hhi_pen
    if hhi > HHI_FULL_PEN:
        warnings.append(
            f"穿透后个股集中度偏高（可见口径 HHI={hhi:.3f}，等效独立持股约 {effective_count} 只）"
        )
        if top_stocks and top_stocks[0]["portfolio_pct"] >= 5:
            t = top_stocks[0]
            suggestions.append(
                f"重仓股 {t['name']} 穿透后占组合 {t['portfolio_pct']:.1f}%，考虑稀释单一个股敞口"
            )

    # ── 2. 基金间两两重叠（Jaccard）──
    overlaps: list[dict] = []
    for i in range(n):
        for j in range(i + 1, n):
            a = {c for c, _, _ in funds[i].get("holdings", [])}
            b = {c for c, _, _ in funds[j].get("holdings", [])}
            if not a or not b:
                continue
            inter = a & b
            union = a | b
            overlaps.append({
                "pair": [funds[i]["code"], funds[j]["code"]],
                "pair_names": [funds[i]["name"], funds[j]["name"]],
                "common": len(inter),
                "jaccard": round(len(inter) / len(union), 3) if union else 0.0,
                "common_names": sorted(
                    {nm for c, nm, _ in funds[i].get("holdings", []) if c in inter}
                )[:5],
            })
    overlaps.sort(key=lambda x: (-x["common"], -x["jaccard"]))
    mean_overlap = (
        sum(o["jaccard"] for o in overlaps) / len(overlaps) if overlaps else 0.0
    )
    ovl_pen = min(OVL_PEN_MAX, mean_overlap * 60.0)
    score -= ovl_pen
    if overlaps and overlaps[0]["common"] >= 5:
        o = overlaps[0]
        shared = "、".join(o["common_names"][:3]) or "多只重仓股"
        warnings.append(
            f"基金 {o['pair_names'][0]} 与 {o['pair_names'][1]} 十大重仓重叠 {o['common']} 只"
            f"（如 {shared}），接近重复暴露"
        )
        suggestions.append(
            f"{o['pair_names'][0]} 与 {o['pair_names'][1]} 重叠度高，二选一降低伪分散"
        )

    # ── 3. 经理/公司集中度（等权口径）──
    def _concentrate(key: str) -> dict[str, float]:
        acc: dict[str, int] = defaultdict(int)
        for f in funds:
            k = (f.get(key) or "").strip()
            if k:
                acc[k] += 1
        total = sum(acc.values()) or 1
        return {k: round(v / total, 4) for k, v in sorted(acc.items(), key=lambda x: -x[1])}

    manager_conc = _concentrate("manager")
    company_conc = _concentrate("company")
    mgr_pen = comp_pen = 0.0
    if manager_conc:
        tm, wm = next(iter(manager_conc.items()))
        if wm >= MGR_MAJOR:
            mgr_pen = 15.0
            warnings.append(
                f"组合 {wm * 100:.0f}% 基金出自同一经理（{tm}），经理个人是最大单点风险"
            )
            suggestions.append(f"将 {tm} 管理的产品占比降至 40% 以内，或补入其他经理产品")
        elif wm >= MGR_MINOR:
            mgr_pen = 8.0
            warnings.append(f"{wm * 100:.0f}% 仓位集中于经理 {tm}，关注离任/风格漂移信号")
    if company_conc:
        tc, wc = next(iter(company_conc.items()))
        if wc >= COMP_MAJOR:
            comp_pen = 10.0
            warnings.append(f"{wc * 100:.0f}% 基金属同一家公司（{tc}），公司层面风险未分散")
    score -= mgr_pen + comp_pen

    # ── 4. 基金数量 ──
    if n == 1:
        score -= SINGLE_FUND_PEN

    score = round(max(0.0, score), 1)
    grade = "优" if score >= 80 else "良" if score >= 60 else "中" if score >= 40 else "差"

    return {
        "fund_count": n,
        "matched_count": len(matched),
        "score": score,
        "grade": grade,
        "hhi": round(hhi, 4),
        "effective_stock_count": effective_count,
        "top_stocks": top_stocks,
        "overlaps": overlaps[:15],
        "mean_jaccard": round(mean_overlap, 3),
        "manager_concentration": manager_conc,
        "company_concentration": company_conc,
        "warnings": warnings,
        "suggestions": suggestions,
        "deductions": {
            "hhi": round(hhi_pen, 1), "overlap": round(ovl_pen, 1),
            "manager": mgr_pen, "company": comp_pen,
            "single_fund": SINGLE_FUND_PEN if n == 1 else 0.0,
        },
    }


class PortfolioXrayService:
    """组合 X 光透视（库内数据装配 + xray_score 纯函数）"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def xray(self, fund_ids: Optional[list[int]] = None) -> dict:
        stmt = select(Fund).where(Fund.status == "active")
        if fund_ids:
            stmt = stmt.where(Fund.id.in_(fund_ids))
        funds = list((await self.db.execute(stmt)).scalars().all())
        if not funds:
            raise ValueError("基金池为空，无法透视")

        holdings_map = await _latest_quarter_holdings(
            self.db, [f.id for f in funds]
        )
        # 经理（最新一条记录）
        mgr_map: dict[int, str] = {}
        mgr_rows = (await self.db.execute(
            select(FundManagerRecord)
            .where(FundManagerRecord.fund_id.in_([f.id for f in funds]))
        )).scalars().all()
        for m in mgr_rows:
            mgr_map.setdefault(m.fund_id, m.manager_name or "")

        payloads = []
        for f in funds:
            holds = holdings_map.get(f.id, [])
            payloads.append({
                "code": f.code,
                "name": f.name or f.code,
                "manager": mgr_map.get(f.id, ""),
                "company": "",
                "holdings": [
                    (h.stock_code, h.stock_name, float(h.ratio)) for h in holds
                ],
            })

        result = xray_score(payloads)
        result["funds_without_holdings"] = [
            f.code for f in funds if not holdings_map.get(f.id)
        ]
        return result

    @staticmethod
    def summary_md(r: dict) -> str:
        lines = [
            "## 🔦 组合 X 光透视",
            "",
            f"**多样化评分**: {r['score']}（{r['grade']}）｜基金 {r['fund_count']} 只"
            f"（有效持仓 {r['matched_count']}）｜穿透 HHI {r['hhi']}"
            f"（等效持股 ≈{r['effective_stock_count']} 只）",
            "",
        ]
        if r["warnings"]:
            lines.append("### 风险提示")
            lines += [f"- {w}" for w in r["warnings"]]
            lines.append("")
        if r["suggestions"]:
            lines.append("### 优化建议")
            lines += [f"- {s}" for s in r["suggestions"]]
            lines.append("")
        lines.append("### 穿透后个股 Top")
        lines.append("")
        lines.append("| 股票 | 组合占比 |")
        lines.append("|------|----------|")
        for t in r["top_stocks"][:10]:
            lines.append(f"| {t['name']}({t['code']}) | {t['portfolio_pct']}% |")
        if r["funds_without_holdings"]:
            lines.append("")
            lines.append(
                f"*注：{len(r['funds_without_holdings'])} 只基金暂无持仓数据未计入穿透*"
            )
        return "\n".join(lines)
