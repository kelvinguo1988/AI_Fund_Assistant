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


# ── 自动全量回测（2026-08-30）────────────────────────────────────────

def _result_to_out(r) -> dict:
    from backend.schemas.backtest import BacktestBatchItem
    return BacktestBatchItem(
        fund_id=r.fund_id, fund_code=r.fund_code, fund_name=r.fund_name,
        period=r.period, effectiveness_window=r.effectiveness_window,
        total_nav_return=r.total_nav_return,
        total_strategy_return=r.total_strategy_return,
        excess_return=r.excess_return, max_drawdown=r.max_drawdown,
        signal_count=r.signal_count,
        avg_effectiveness=r.avg_effectiveness,
        buy_effectiveness=r.buy_effectiveness,
        sell_effectiveness=r.sell_effectiveness,
        effectiveness_rate=r.effectiveness_rate,
        finished_at=str(r.finished_at) if r.finished_at else None,
        error=r.error, ok=r.ok,
    ).model_dump(mode="json")


@router.get("/batch/results", response_model=ApiResponse[list[dict]])
async def list_batch_results(db: AsyncSession = Depends(get_db)):
    """自动回测的逐基金结果（按完成时间倒序）"""
    from sqlalchemy import select
    from backend.models.backtest_result import BacktestResult
    rows = (await db.execute(
        select(BacktestResult).order_by(BacktestResult.finished_at.desc())
    )).scalars().all()
    return ApiResponse(data=[_result_to_out(r) for r in rows])


@router.delete("/batch/results", response_model=ApiResponse[int])
async def clear_batch_results(db: AsyncSession = Depends(get_db)):
    """清空批量回测结果"""
    from backend.services.auto_backtest_service import AutoBacktestService
    return ApiResponse(data=await AutoBacktestService.clear_results(db))


@router.get("/batch/config")
async def get_auto_config(db: AsyncSession = Depends(get_db)):
    """自动回测配置（开关/间隔）"""
    from backend.services.auto_backtest_service import get_auto_config
    return ApiResponse(data=await get_auto_config(db))


@router.put("/batch/config")
async def update_auto_config(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """更新自动回测配置（enabled/interval_min/interval_max），保存后立即重载调度"""
    from backend.services.auto_backtest_service import (
        get_auto_config, CFG_ENABLED, CFG_MIN_INTERVAL, CFG_MAX_INTERVAL,
    )
    from backend.models.system_config import SystemConfig

    allowed = {
        CFG_ENABLED: lambda v: str(bool(v)).lower(),
        CFG_MIN_INTERVAL: lambda v: str(max(1.0, float(v))),
        CFG_MAX_INTERVAL: lambda v: str(max(1.0, float(v))),
    }
    for key, conv in allowed.items():
        if key in body:
            value = conv(body[key])
            row = (await db.execute(
                select(SystemConfig).where(SystemConfig.config_key == key)
            )).scalars().first()
            if row:
                row.config_value = value
            else:
                db.add(SystemConfig(config_key=key, config_value=value))
    await db.commit()
    # 开关变更立即生效（注册/移除周日任务）
    from backend.scheduler.task_scheduler import task_scheduler
    await task_scheduler.register_auto_backtest_now()
    return ApiResponse(data=await get_auto_config(db))


@router.post("/batch/run")
async def trigger_batch_backtest(db: AsyncSession = Depends(get_db)):
    """手动触发一次全量回测（后台执行，逐只落库）"""
    from backend.services.auto_backtest_service import AutoBacktestService
    from backend.database import async_session_factory
    import asyncio

    # 2026-08-30 复查修复：原 force=True 会绕过防重入，
    # 连点两次"立即全量回测"造成两轮并发打爆数据源
    if AutoBacktestService._running:
        raise HTTPException(status_code=409, detail="全量回测正在运行中，请等待完成")

    async def _job():
        async with async_session_factory() as session:
            svc = AutoBacktestService(session)
            await svc.run_full_backtest()

    asyncio.create_task(_job())
    return ApiResponse(data={"accepted": True})
