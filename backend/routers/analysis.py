from __future__ import annotations
"""分析结果查询 + 手动触发路由"""

import json
import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.schemas.common import ApiResponse
from backend.schemas.analysis import FactorScore, AnalysisResultOut
from backend.schemas.market import MarketSummaryOut, SignalSummary, MarketCapitalFlow, SectorFlowRanking, HSGTFlow, MarketAdvDecline, MarketTurnover

CACHE_KEY_MARKET = "market_summary"

logger = logging.getLogger(__name__)
router = APIRouter()


def _result_to_out(r: AnalysisResult, fund: Fund | None = None) -> AnalysisResultOut:
    """ORM → Schema 转换"""
    factor_scores: list[FactorScore] = []
    try:
        raw = json.loads(r.factor_scores) if isinstance(r.factor_scores, str) else r.factor_scores
        if isinstance(raw, dict):
            for code, val in raw.items():
                if isinstance(val, dict):
                    factor_scores.append(FactorScore(
                        factor_code=code,
                        factor_name=val.get("name", code),
                        raw_value=val.get("raw_value", 0),
                        score=val.get("score", 0),
                        direction=val.get("direction", "positive"),
                    ))
                else:
                    factor_scores.append(FactorScore(
                        factor_code=code,
                        factor_name=code,
                        raw_value=0,
                        score=float(val),
                        direction="positive",
                    ))
    except (json.JSONDecodeError, TypeError):
        pass

    return AnalysisResultOut(
        id=r.id,
        fund_id=r.fund_id,
        fund_code=fund.code if fund else "",
        fund_name=fund.name if fund else "",
        analysis_date=r.analysis_date,
        weighted_score=r.weighted_score,
        signal_direction=r.signal_direction,
        signal_strength=r.signal_strength or "",
        operation_advice=r.operation_advice or "",
        equity_ratio=getattr(r, "equity_ratio", 0.5),
        factor_scores=factor_scores,
        created_at=r.created_at,
    )


@router.get("", response_model=ApiResponse[list[AnalysisResultOut]])
async def query_analysis(
    date_param: Optional[str] = Query(None, alias="date"),
    fund_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """查询分析结果"""
    stmt = select(AnalysisResult).order_by(AnalysisResult.analysis_date.desc())
    if date_param:
        stmt = stmt.where(AnalysisResult.analysis_date == date_param)
    if fund_id:
        stmt = stmt.where(AnalysisResult.fund_id == fund_id)

    result = await db.execute(stmt)
    results = result.scalars().all()

    out_list = []
    for r in results:
        fund_result = await db.execute(select(Fund).where(Fund.id == r.fund_id))
        fund = fund_result.scalars().first()
        out_list.append(_result_to_out(r, fund))

    return ApiResponse(data=out_list)


@router.get("/latest", response_model=ApiResponse[list[AnalysisResultOut]])
async def get_latest_analysis(db: AsyncSession = Depends(get_db)):
    """获取最新分析结果"""
    # 获取最新日期
    from sqlalchemy import func
    result = await db.execute(select(func.max(AnalysisResult.analysis_date)))
    latest_date = result.scalar()

    if not latest_date:
        return ApiResponse(data=[])

    stmt = select(AnalysisResult).where(AnalysisResult.analysis_date == latest_date)
    result = await db.execute(stmt)
    results = result.scalars().all()

    out_list = []
    for r in results:
        fund_result = await db.execute(select(Fund).where(Fund.id == r.fund_id))
        fund = fund_result.scalars().first()
        out_list.append(_result_to_out(r, fund))

    return ApiResponse(data=out_list)


@router.get("/summary", response_model=ApiResponse[MarketSummaryOut])
async def get_market_summary(db: AsyncSession = Depends(get_db)):
    """获取市场概况汇总 — 先展示缓存，后台刷新"""
    from sqlalchemy import func

    # 1. 获取最新分析日期
    result = await db.execute(select(func.max(AnalysisResult.analysis_date)))
    latest_date = result.scalar()

    today_str = date.today().isoformat()
    summary_date = latest_date.isoformat() if latest_date else today_str

    # 2. 获取当日分析结果（始终实时查询，DB 查询很快）
    stmt = select(AnalysisResult).where(AnalysisResult.analysis_date == latest_date)
    result = await db.execute(stmt)
    analysis_list = result.scalars().all()

    signal_summary = SignalSummary(total=len(analysis_list))
    out_list: list[AnalysisResultOut] = []

    for r in analysis_list:
        fund_result = await db.execute(select(Fund).where(Fund.id == r.fund_id))
        fund = fund_result.scalars().first()
        out = _result_to_out(r, fund)
        out_list.append(out)
        if out.signal_direction == "buy":
            signal_summary.buy_count += 1
        elif out.signal_direction == "sell":
            signal_summary.sell_count += 1
        else:
            signal_summary.hold_count += 1

    out_list.sort(key=lambda x: x.weighted_score, reverse=True)
    signal_summary.top_buy = [o for o in out_list if o.signal_direction == "buy"][:5]
    signal_summary.top_sell = [o for o in out_list if o.signal_direction == "sell"][-5:]

    # 3. 尝试返回缓存的行情数据
    from backend.services.fund_cache_service import get_cached_json
    market_cache, updated_at = await get_cached_json(db, CACHE_KEY_MARKET)

    if market_cache and updated_at:
        summary = MarketSummaryOut(
            date=summary_date,
            signals=signal_summary,
            market_flow=MarketCapitalFlow(**market_cache["market_flow"]) if market_cache.get("market_flow") else None,
            sector_flow=[SectorFlowRanking(**s) for s in market_cache.get("sector_flow", [])],
            hsgt_flow=HSGTFlow(**market_cache["hsgt_flow"]) if market_cache.get("hsgt_flow") else None,
            adv_decline=MarketAdvDecline(**market_cache["adv_decline"]) if market_cache.get("adv_decline") else None,
            turnover=MarketTurnover(**market_cache["turnover"]) if market_cache.get("turnover") else None,
            updated_at=updated_at,
        )
        return ApiResponse(data=summary)

    # 4. 无缓存 — 全量拉取行情数据
    from backend.services.market_service import MarketService
    svc = MarketService()
    market_flow = await svc.get_market_capital_flow()
    sector_flow_raw = await svc.get_sector_flow_rankings()
    hsgt_flow = await svc.get_hsgt_flow()
    adv_decline = await svc.get_market_adv_decline()
    turnover = await svc.get_market_turnover()

    sector_flow_list = list(sector_flow_raw.values())

    # 5. 写入缓存
    from backend.services.fund_cache_service import set_cached_json
    cache_data = {
        "market_flow": market_flow.model_dump() if market_flow else None,
        "sector_flow": [s.model_dump() for s in sector_flow_list],
        "hsgt_flow": hsgt_flow.model_dump() if hsgt_flow else None,
        "adv_decline": adv_decline.model_dump() if adv_decline else None,
        "turnover": turnover.model_dump() if turnover else None,
    }
    updated_at = await set_cached_json(db, CACHE_KEY_MARKET, cache_data)

    summary = MarketSummaryOut(
        date=summary_date,
        signals=signal_summary,
        market_flow=market_flow,
        sector_flow=sector_flow_list,
        hsgt_flow=hsgt_flow,
        adv_decline=adv_decline,
        turnover=turnover,
        updated_at=updated_at,
    )
    return ApiResponse(data=summary)


@router.post("/refresh-summary", response_model=ApiResponse[dict])
async def refresh_market_summary(db: AsyncSession = Depends(get_db)):
    """后台刷新行情数据并更新缓存"""
    from backend.services.market_service import MarketService
    from backend.services.fund_cache_service import set_cached_json

    # 清除 MarketService 内存缓存，确保获取最新行情
    MarketService.clear_cache()
    svc = MarketService()
    market_flow = await svc.get_market_capital_flow()
    sector_flow_raw = await svc.get_sector_flow_rankings()
    hsgt_flow = await svc.get_hsgt_flow()
    adv_decline = await svc.get_market_adv_decline()
    turnover = await svc.get_market_turnover()

    sector_flow_list = list(sector_flow_raw.values())

    cache_data = {
        "market_flow": market_flow.model_dump() if market_flow else None,
        "sector_flow": [s.model_dump() for s in sector_flow_list],
        "hsgt_flow": hsgt_flow.model_dump() if hsgt_flow else None,
        "adv_decline": adv_decline.model_dump() if adv_decline else None,
        "turnover": turnover.model_dump() if turnover else None,
    }
    updated_at = await set_cached_json(db, CACHE_KEY_MARKET, cache_data)

    return ApiResponse(data={"updated_at": updated_at})


@router.post("/trigger", response_model=ApiResponse[list[AnalysisResultOut]])
async def trigger_analysis(
    body: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
):
    """手动触发分析

    body: {"fund_ids": [1, 2, 3]} 或空对象表示全部
    """
    try:
        from backend.config import settings
        from backend.services.analysis_service import AnalysisService
        svc = AnalysisService(db, tushare_token=settings.TUSHARE_TOKEN)
        fund_ids = body.get("fund_ids") if body else None
        results = await svc.run_analysis(fund_ids=fund_ids)
        return ApiResponse(data=results)
    except Exception as e:
        logger.error(f"触发分析失败: {e}")
        raise HTTPException(status_code=500, detail=f"分析执行失败: {str(e)}")


@router.post("/trigger-stream")
async def trigger_analysis_stream(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """流式触发分析 — 通过 SSE 逐批推送分析结果

    Body: {"fund_ids": [1, 2, 3]} 或空对象表示全部
    Returns: text/event-stream
    """
    from backend.config import settings
    from backend.services.analysis_service import AnalysisService

    svc = AnalysisService(db, tushare_token=settings.TUSHARE_TOKEN)

    body = await request.json() if request.headers.get("content-type") else None
    fund_ids = body.get("fund_ids") if isinstance(body, dict) else None

    async def _event_stream():
        async for event in svc.run_analysis_streaming(fund_ids=fund_ids):
            yield event
            if await request.is_disconnected():
                break

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
