"""基金 CRUD 路由"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.common import ApiResponse
from backend.schemas.fund import FundCreate, FundUpdate, FundOut
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
