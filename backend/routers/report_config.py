"""报告配置 CRUD 路由"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.report_config import ReportConfig
from backend.schemas.common import ApiResponse
from backend.schemas.report_config import ReportConfigOut, ReportConfigUpdate

router = APIRouter()


@router.get("", response_model=ApiResponse[list[ReportConfigOut]])
async def list_report_config(db: AsyncSession = Depends(get_db)):
    """获取报告配置列表"""
    result = await db.execute(select(ReportConfig).order_by(ReportConfig.sort_order))
    configs = result.scalars().all()
    return ApiResponse(data=[ReportConfigOut.model_validate(c) for c in configs])


@router.put("", response_model=ApiResponse[list[ReportConfigOut]])
async def batch_update_report_config(
    body: list[ReportConfigUpdate],
    db: AsyncSession = Depends(get_db),
):
    """批量更新报告配置 + 排序"""
    updated = []
    for item in body:
        result = await db.execute(select(ReportConfig).where(ReportConfig.id == item.id))
        config = result.scalars().first()
        if config is None:
            continue

        if item.enabled is not None:
            config.enabled = item.enabled
        if item.sort_order is not None:
            config.sort_order = item.sort_order

        updated.append(config)

    await db.commit()

    # 重新查询以获取最新数据
    result = await db.execute(select(ReportConfig).order_by(ReportConfig.sort_order))
    configs = result.scalars().all()
    return ApiResponse(data=[ReportConfigOut.model_validate(c) for c in configs])
