"""信号回测路由"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.common import ApiResponse
from backend.schemas.backtest import BacktestSummary
from backend.services.backtest_service import BacktestService

router = APIRouter()


@router.get("/{fund_id}", response_model=ApiResponse[BacktestSummary])
async def run_backtest(
    fund_id: int,
    period: int = Query(365, ge=30, le=1500, description="回测天数（30~1500）"),
    effectiveness_window: int = Query(5, ge=1, le=20, description="信号有效性评估窗口(交易日)"),
    db: AsyncSession = Depends(get_db),
):
    """运行信号回测

    将基金历史净值序列与分析信号按日期对齐，模拟仓位策略累计收益。
    """
    svc = BacktestService(db)
    result = await svc.run_backtest(
        fund_id=fund_id, period=period, effectiveness_window=effectiveness_window
    )
    if result is None:
        raise HTTPException(status_code=404, detail="基金不存在或无净值数据")
    return ApiResponse(data=result)
