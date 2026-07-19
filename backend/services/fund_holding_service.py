"""基金季度持仓服务 — 从 AKShare fund_portfolio_hold_em 获取并存入数据库

数据来源：AKShare 封装的天天基金季度持仓数据。
每只基金每季度约 10-20 条持仓记录。
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import akshare as ak
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.fund_holding import FundHolding
from backend.utils.concurrency import run_with_timeout

logger = logging.getLogger(__name__)

# 单次持仓查询超时（秒）。akshare fund_portfolio_hold_em 实测 3-8s，
# 25s 留足缓冲，避免网络波动误杀；超时后线程隔离不阻塞其他调用。
_HOLDING_TIMEOUT: float = 25.0


async def refresh_holdings(db: AsyncSession, fund_id: int, fund_code: str) -> list[FundHolding]:
    """抓取基金近2年持仓并存入数据库

    关键改进：
    - 跳过当前年份（数据通常未发布，akshare 收到非 JSON 响应导致 decode 错误）
    - 从去年开始往前查询，数据存在性由近及远递减
    - JSON 解码错误单独处理（"Can not decode value starting with ';'"），
      视为数据不可用跳过而非异常，减少日志噪音
    """
    from json.decoder import JSONDecodeError

    current_year = datetime.now().year
    # 跳过当前年份 — Q2 数据通常在 7 月底 / 8 月才发布，
    # 之前请求当年数据 akshare 会收到 JS 脚本而非 JSON，触发解码错误
    years = [str(current_year - 1)]  # 只查去年

    for year in years:
        try:
            # 使用独立线程池 + 强制超时，避免 akshare 卡住无限挂起
            df = await run_with_timeout(
                ak.fund_portfolio_hold_em,
                symbol=fund_code,
                date=year,
                timeout=_HOLDING_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("获取基金 %s %s 年持仓超时（%ss），跳过", fund_code, year, _HOLDING_TIMEOUT)
            continue
        except JSONDecodeError as e:
            # akshare 内部 JSON 解析失败 — 通常是数据尚未发布或该基金不支持此接口
            logger.debug("基金 %s %s 年持仓数据不可用（JSON解析失败）: %s", fund_code, year, e)
            continue
        except ValueError as e:
            # 某些情况下 akshare 会重新包装 JSONDecodeError 为 ValueError
            msg = str(e)
            if "Can not decode" in msg:
                logger.debug("基金 %s %s 年持仓数据不可用（格式异常）: %s", fund_code, year, msg[:60])
                continue
            logger.warning("获取基金 %s %s 年持仓失败: %s", fund_code, year, e)
            continue
        except Exception as e:
            logger.warning("获取基金 %s %s 年持仓失败: %s", fund_code, year, e)
            continue

        if df is None or df.empty:
            continue

        for _, row in df.iterrows():
            quarter = str(row.get("季度", ""))
            if not quarter:
                continue

            stmt = select(FundHolding).where(
                FundHolding.fund_id == fund_id,
                FundHolding.stock_code == str(row.get("股票代码", "")),
                FundHolding.quarter_label == quarter,
            )
            existing = (await db.execute(stmt)).scalars().first()
            if existing:
                continue

            holding = FundHolding(
                fund_id=fund_id,
                stock_code=str(row.get("股票代码", "")),
                stock_name=str(row.get("股票名称", "")),
                ratio=_to_float(row.get("占净值比例")),
                shares=_to_float(row.get("持股数")),
                market_value=_to_float(row.get("持仓市值")),
                quarter_label=quarter,
                report_date="",
            )
            db.add(holding)

    await db.commit()

    stmt = (
        select(FundHolding)
        .where(FundHolding.fund_id == fund_id)
        .order_by(FundHolding.quarter_label.desc(), FundHolding.id)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest_holdings(db: AsyncSession, fund_id: int, limit: int = 10) -> list[FundHolding]:
    """获取基金最新季度 top N 持仓"""
    stmt = (
        select(FundHolding.quarter_label)
        .where(FundHolding.fund_id == fund_id)
        .distinct()
        .order_by(FundHolding.quarter_label.desc())
    )
    quarters = (await db.execute(stmt)).scalars().all()
    if not quarters:
        return []

    latest_quarter = quarters[0]
    stmt = (
        select(FundHolding)
        .where(FundHolding.fund_id == fund_id, FundHolding.quarter_label == latest_quarter)
        .order_by(FundHolding.ratio.desc().nullslast())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def compute_holding_changes(
    db: AsyncSession, fund_id: int
) -> Optional[dict]:
    """计算基金最新两个季度的持仓变更"""
    stmt = (
        select(FundHolding.quarter_label)
        .where(FundHolding.fund_id == fund_id)
        .distinct()
        .order_by(FundHolding.quarter_label.desc())
    )
    quarters = (await db.execute(stmt)).scalars().all()
    if len(quarters) < 2:
        return None

    q_latest, q_prev = quarters[0], quarters[1]

    async def _stocks_set(label: str) -> set[str]:
        s = select(FundHolding.stock_code).where(
            FundHolding.fund_id == fund_id, FundHolding.quarter_label == label
        )
        return {r[0] for r in (await db.execute(s)).all()}

    async def _stocks_detail(label: str) -> list[dict]:
        s = select(FundHolding).where(
            FundHolding.fund_id == fund_id, FundHolding.quarter_label == label
        ).order_by(FundHolding.ratio.desc().nullslast())
        rows = (await db.execute(s)).scalars().all()
        return [{"stock_code": r.stock_code, "stock_name": r.stock_name, "ratio": r.ratio} for r in rows]

    latest_set = await _stocks_set(q_latest)
    prev_set = await _stocks_set(q_prev)

    added_codes = latest_set - prev_set
    removed_codes = prev_set - latest_set

    latest_all = await _stocks_detail(q_latest)
    prev_all = await _stocks_detail(q_prev)

    added = [s for s in latest_all if s["stock_code"] in added_codes]
    removed = [s for s in prev_all if s["stock_code"] in removed_codes]

    return {
        "latest_quarter": q_latest,
        "previous_quarter": q_prev,
        "added": added,
        "removed": removed,
    }


def _to_float(v) -> Optional[float]:
    if v is None or v == "" or v == "None" or v == "nan":
        return None
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None
