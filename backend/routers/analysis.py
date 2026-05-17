from __future__ import annotations
"""分析结果查询 + 手动触发路由"""

import json
import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.analysis_result import AnalysisResult
from backend.models.fund import Fund
from backend.schemas.common import ApiResponse
from backend.schemas.analysis import FactorScore, AnalysisResultOut

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
