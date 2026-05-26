"""调度计划 CRUD 路由"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.schedule import Schedule
from backend.schemas.common import ApiResponse
from backend.schemas.schedule import ScheduleCreate, ScheduleUpdate, ScheduleOut

router = APIRouter()


@router.get("", response_model=ApiResponse[list[ScheduleOut]])
async def list_schedules(db: AsyncSession = Depends(get_db)):
    """获取调度列表"""
    result = await db.execute(select(Schedule).order_by(Schedule.id))
    schedules = result.scalars().all()
    return ApiResponse(data=[ScheduleOut.model_validate(s) for s in schedules])


@router.post("", response_model=ApiResponse[ScheduleOut])
async def create_schedule(
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
):
    """新增调度"""
    sched = Schedule(
        name=body.name,
        cron_expr=body.cron_expr,
        time_point=body.time_point,
        task_type=body.task_type,
        channel_id=body.channel_id,
        enabled=body.enabled,
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)

    # 热更新调度器，使新调度立即生效
    try:
        from backend.scheduler.task_scheduler import task_scheduler
        await task_scheduler.reload_jobs()
    except ImportError:
        pass

    return ApiResponse(data=ScheduleOut.model_validate(sched))


@router.put("/{schedule_id}", response_model=ApiResponse[ScheduleOut])
async def update_schedule(
    schedule_id: int,
    body: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新调度"""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sched = result.scalars().first()
    if sched is None:
        raise HTTPException(status_code=404, detail="调度不存在")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(sched, key, value)

    await db.commit()
    await db.refresh(sched)

    # 热更新调度器
    try:
        from backend.scheduler.task_scheduler import task_scheduler
        await task_scheduler.reload_jobs()
    except Exception:
        pass

    return ApiResponse(data=ScheduleOut.model_validate(sched))


@router.delete("/{schedule_id}", response_model=ApiResponse[None])
async def delete_schedule(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除调度"""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sched = result.scalars().first()
    if sched is None:
        raise HTTPException(status_code=404, detail="调度不存在")

    await db.delete(sched)
    await db.commit()

    # 热更新调度器
    try:
        from backend.scheduler.task_scheduler import task_scheduler
        await task_scheduler.reload_jobs()
    except Exception:
        pass

    return ApiResponse()
