"""基金 CRUD 路由"""

import asyncio
import logging
import random
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.common import ApiResponse
from backend.schemas.fund import (
    FundCreate, FundUpdate, FundOut, FundPeriodReturn,
    FundHoldingOut, FundManagerOut, FundChangeSummary,
)
from backend.services.fund_detail_service import fetch_period_returns
from backend.services.fund_holding_service import get_latest_holdings, refresh_holdings
from backend.services.fund_manager_service import get_current_managers, refresh_managers
from backend.services.fund_change_detector import get_fund_changes
from backend.services.fund_service import FundService

router = APIRouter()


@router.get("", response_model=ApiResponse[list[FundOut]])
async def list_funds(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """获取基金列表（支持 ?status=active 筛选）"""
    svc = FundService(db)
    funds = await svc.list_funds(status=status)
    return ApiResponse(data=[FundOut.model_validate(f) for f in funds])


@router.post("", response_model=ApiResponse[FundOut])
async def create_fund(
    body: FundCreate,
    db: AsyncSession = Depends(get_db),
):
    """新增基金"""
    svc = FundService(db)
    try:
        fund = await svc.create_fund(body)
        return ApiResponse(data=FundOut.model_validate(fund))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{fund_id}", response_model=ApiResponse[FundOut])
async def update_fund(
    fund_id: int,
    body: FundUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新基金"""
    svc = FundService(db)
    fund = await svc.update_fund(fund_id, body)
    if fund is None:
        raise HTTPException(status_code=404, detail="基金不存在")
    return ApiResponse(data=FundOut.model_validate(fund))


@router.delete("/{fund_id}", response_model=ApiResponse[None])
async def delete_fund(
    fund_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除基金"""
    svc = FundService(db)
    ok = await svc.delete_fund(fund_id)
    if not ok:
        raise HTTPException(status_code=404, detail="基金不存在")
    return ApiResponse()


@router.post("/import", response_model=ApiResponse[dict])
async def import_funds(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量导入基金

    body: {
      "items": [
        {"code": "510300", "name": "沪深300ETF", "tags": "宽基,大盘"},
        {"code": "018495", "name": "融通产业趋势臻选股票C"}
      ]
    }
    已有代码自动跳过，返回导入摘要。
    """
    items = body.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="items 不能为空")
    svc = FundService(db)
    result = await svc.batch_import(items)
    return ApiResponse(data=result)


@router.get("/{fund_id}/holdings", response_model=ApiResponse[list[FundHoldingOut]])
async def get_fund_holdings(
    fund_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取基金最新季度持仓"""
    holdings = await get_latest_holdings(db, fund_id)
    return ApiResponse(data=[FundHoldingOut.model_validate(h) for h in holdings])


@router.get("/{fund_id}/manager", response_model=ApiResponse[list[FundManagerOut]])
async def get_fund_manager(
    fund_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取基金当前经理信息"""
    managers = await get_current_managers(db, fund_id)
    return ApiResponse(data=[FundManagerOut.model_validate(m) for m in managers])


@router.post("/refresh-details", response_model=ApiResponse[dict])
async def refresh_all_details(
    db: AsyncSession = Depends(get_db),
):
    """刷新所有活跃基金的持仓+经理数据（不带标签写入）"""
    svc = FundService(db)
    funds = await svc.list_funds(status="active")
    results: list[dict] = []
    for i, f in enumerate(funds):
        try:
            await refresh_holdings(db, f.id, f.code)
            await refresh_managers(db, f.id, f.code)
            results.append({"code": f.code, "name": f.name, "status": "ok"})
            logger.info("刷新基金 %s 详情完成 (%d/%d)", f.code, i + 1, len(funds))
        except Exception as e:
            logger.warning("刷新基金 %s 详情异常: %s", f.code, e)
            results.append({"code": f.code, "name": f.name, "error": str(e)})
        # 反爬：每只基金间隔 3-6 秒
        if i < len(funds) - 1:
            await asyncio.sleep(random.uniform(3, 6))
    await db.commit()
    return ApiResponse(data={"total": len(funds), "results": results})


@router.get("/change-summary", response_model=ApiResponse[list[FundChangeSummary]])
async def get_funds_change_summary(
    db: AsyncSession = Depends(get_db),
):
    """获取所有活跃基金的变更摘要（持仓调仓+经理变更）"""
    svc = FundService(db)
    funds = await svc.list_funds(status="active")
    data: list[FundChangeSummary] = []
    for f in funds:
        try:
            changes = await get_fund_changes(db, f.id)
            data.append(FundChangeSummary(
                fund_id=f.id,
                fund_code=f.code,
                fund_name=f.name,
                **changes,
            ))
        except Exception as e:
            logger.warning("获取基金 %s 变更摘要异常: %s", f.code, e)
    return ApiResponse(data=data)


@router.get("/detail", response_model=ApiResponse[list[FundPeriodReturn]])
async def get_funds_detail(
    db: AsyncSession = Depends(get_db),
):
    """获取基金池内所有活跃基金的阶段涨幅"""
    svc = FundService(db)
    funds = await svc.list_funds(status="active")
    if not funds:
        return ApiResponse(data=[])

    codes = [f.code for f in funds]
    name_map = {f.code: f.name for f in funds}
    returns = await fetch_period_returns(codes)

    data = [
        FundPeriodReturn(
            code=code,
            name=name_map.get(code, ""),
            **returns.get(code, {}),
        )
        for code in codes
    ]
    return ApiResponse(data=data)


@router.post("/{fund_id}/refresh-themes", response_model=ApiResponse[FundOut])
async def refresh_fund_themes(
    fund_id: int,
    db: AsyncSession = Depends(get_db),
):
    """重新抓取天天基金相关主题并更新标签"""
    svc = FundService(db)
    fund = await svc.refresh_themes(fund_id)
    if fund is None:
        raise HTTPException(status_code=404, detail="基金不存在")
    return ApiResponse(data=FundOut.model_validate(fund))


@router.patch("/batch", response_model=ApiResponse[None])
async def batch_update_funds(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """批量启用/停用基金

    body: {"ids": [1, 2, 3], "action": "active" / "disabled"}
    """
    ids = body.get("ids", [])
    action = body.get("action", "")
    if not ids or action not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="参数错误：ids 非空且 action 为 active/disabled")
    svc = FundService(db)
    await svc.batch_update_status(ids, action)
    return ApiResponse()
