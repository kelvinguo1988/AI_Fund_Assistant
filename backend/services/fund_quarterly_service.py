"""fund_quarterly 写入链路 — 复用 pingzhongdata 扩展数据落季度记录

为什么需要：第零层标的质量过滤（`engines/quality_filter.py`）的四项检查
—— 清盘否决、规模冲击、仓位漂移、机构认可度 —— 全部读 `fund_quarterly`，
而这张表此前全仓零写入，四项检查一直按中性处理。

数据不额外请求：`extended_detail` 刷新时已经把每只基金的 pingzhongdata JS
文本抓到并解析过一次，这里只是把同源的四组数据按报告期对齐落库
（资产配置 / 规模变动 / 持有人结构 / 申赎与总份额），零新增网络请求。

单位口径（quality_filter 阈值全是绝对值，错一个量级检查就永久失效）：
- `fund_size` 元：pingzhongdata 的规模是亿元，× 1e8
- `stock_position_ratio` / `institution_holding_ratio`：%（94.54 表示 94.54%）
- `insider_holding_shares` 份：内部持有比例% × 总份额（亿份 × 1e8）
"""

import logging
from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund_quarterly import FundQuarterly

logger = logging.getLogger(__name__)

# 亿元 / 亿份 → 元 / 份
_YI = 1e8


# 报告期月份 → 披露截止后的滞后月数（宁可晚不可早）：
# 季报须在季末后 15 个工作日内披露（约次月底）→ +2；
# 半年报 60 日内（约 8 月底）、年报 90 日内（约 3 月底）→ +3。
_LAG_MONTHS = {3: 2, 6: 3, 9: 2, 12: 3}


def report_effective_date(report_date: str) -> str:
    """报告期数据生效日 = 披露截止后的第一个工作日

    落库的是「什么时候才可以拿这份数据做判定」，不是报告期本身：3-31 的一季报
    最快 4 月底才披露，5 月第一个工作日起才算已公开。周末顺延到周一；法定长假
    未建模，误差在几天量级，只影响"早几天开始提示规模冲击/漂移"，不参与回测取数。
    """
    try:
        y, m, d = (int(x) for x in report_date.split("-"))
    except (ValueError, AttributeError):
        return report_date
    total = m + _LAG_MONTHS.get(m, 2)
    year = y + (total - 1) // 12
    month = (total - 1) % 12 + 1
    eff = date(year, month, 1)
    while eff.weekday() >= 5:  # 5=周六 6=周日
        eff = date.fromordinal(eff.toordinal() + 1)
    return eff.isoformat()


def _series_map(container: Optional[dict], series_name: str) -> dict[str, float]:
    """{报告期: 该系列数值} —— pingzhongdata 用 categories 与 series.data 下标对齐"""
    if not isinstance(container, dict):
        return {}
    cats = container.get("categories") or []
    for s in container.get("series") or []:
        if s.get("name") == series_name:
            return {c: v for c, v in zip(cats, s.get("data") or [])
                    if isinstance(v, (int, float))}
    return {}


def _fluctuation_sizes(container: Optional[dict]) -> dict[str, float]:
    """规模变动 Data_fluctuationScale 的 series 无 name，是 [{y: 亿元, mom: 环比}]"""
    if not isinstance(container, dict):
        return {}
    cats = container.get("categories") or []
    out: dict[str, float] = {}
    for c, s in zip(cats, container.get("series") or []):
        if isinstance(s, dict) and isinstance(s.get("y"), (int, float)):
            out[c] = s["y"]
    return out


def build_quarterly_rows(extended: dict) -> list[dict]:
    """把单只基金的扩展数据（`_parse_extended_data` 产物）合并成按报告期的行

    Returns: [{report_date, effective_date, fund_size, stock_position_ratio,
              institution_holding_ratio, insider_holding_shares}, ...]
    按 report_date 升序；四项数值全空的报告期被丢弃（不留空行占位）。
    """
    asset = extended.get("asset_allocation") or {}
    holder = extended.get("holder_structure") or {}
    fluct = extended.get("fluctuation_scale") or {}
    sedem = extended.get("buy_sedemption") or {}

    stock = _series_map(asset, "股票占净比")
    alloc_nav = _series_map(asset, "净资产")          # 亿元
    sizes = _fluctuation_sizes(fluct)                 # 亿元
    inst = _series_map(holder, "机构持有比例")
    insider_pct = _series_map(holder, "内部持有比例")
    total_shares = _series_map(sedem, "总份额")       # 亿份

    dates = sorted(set(stock) | set(alloc_nav) | set(sizes) | set(inst) | set(insider_pct))

    rows: list[dict] = []
    for rpt in dates:
        size_yi = sizes.get(rpt, alloc_nav.get(rpt))
        shares = total_shares.get(rpt)
        insider_shares = (
            round(insider_pct[rpt] / 100.0 * shares * _YI, 2)
            if rpt in insider_pct and shares is not None else None
        )
        row = {
            "report_date": rpt,
            "effective_date": report_effective_date(rpt),
            "fund_size": round(size_yi * _YI, 2) if size_yi is not None else None,
            "stock_position_ratio": stock.get(rpt),
            "institution_holding_ratio": inst.get(rpt),
            "insider_holding_shares": insider_shares,
        }
        if all(row[k] is None for k in
               ("fund_size", "stock_position_ratio",
                "institution_holding_ratio", "insider_holding_shares")):
            continue
        rows.append(row)
    return rows


async def sync_quarterly_rows(
    db: AsyncSession, fund_id: int, rows: list[dict]
) -> int:
    """按 (fund_id, report_date) upsert 季度记录，返回写入行数

    数值列用 COALESCE 保留旧值：上游某一期暂时取不到某项（如总份额缺失导致
    内部人份额为空）时，不把已落库的历史数值清空。
    """
    from backend.utils.timezone import now_beijing

    if not rows:
        return 0
    now = now_beijing()
    for r in rows:
        values = {"fund_id": fund_id, "created_at": now, "updated_at": now, **r}
        stmt = sqlite_insert(FundQuarterly).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["fund_id", "report_date"],
            set_={
                "effective_date": stmt.excluded.effective_date,
                "fund_size": func.coalesce(stmt.excluded.fund_size, FundQuarterly.fund_size),
                "stock_position_ratio": func.coalesce(
                    stmt.excluded.stock_position_ratio, FundQuarterly.stock_position_ratio),
                "institution_holding_ratio": func.coalesce(
                    stmt.excluded.institution_holding_ratio,
                    FundQuarterly.institution_holding_ratio),
                "insider_holding_shares": func.coalesce(
                    stmt.excluded.insider_holding_shares,
                    FundQuarterly.insider_holding_shares),
                "updated_at": now,
            },
        )
        await db.execute(stmt)
    return len(rows)


async def sync_quarterly_from_extended(
    db: AsyncSession,
    extended_by_code: dict[str, dict],
    fund_id_by_code: dict[str, int],
) -> tuple[int, int]:
    """从扩展详情数据批量落 fund_quarterly

    Args:
        extended_by_code: {code: _parse_extended_data 产物}
        fund_id_by_code: {code: fund_id} —— 只有入池基金才有 id，池外代码跳过

    Returns:
        (基金数, 行数)
    """
    funds = 0
    total_rows = 0
    skipped: list[str] = []
    for code, ext in extended_by_code.items():
        fund_id = fund_id_by_code.get(code)
        if not fund_id:
            continue
        rows = build_quarterly_rows(ext or {})
        if not rows:
            skipped.append(code)
            continue
        total_rows += await sync_quarterly_rows(db, fund_id, rows)
        funds += 1
    await db.commit()
    if skipped:
        logger.info("季度数据无可解析内容: %s", "、".join(skipped[:10]))
    logger.info("fund_quarterly 已同步：%d 只基金 / %d 个报告期", funds, total_rows)
    return funds, total_rows
