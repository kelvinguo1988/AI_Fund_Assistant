"""我的持仓路由 — CRUD + CSV 导入（手动/导入，不接第三方账户同步）

- GET    /api/positions            持仓列表（附最新评分/信号）
- POST   /api/positions            按基金代码建仓/覆盖更新
- PUT    /api/positions/{id}       更新份额/成本
- DELETE /api/positions/{id}       删除
- POST   /api/positions/import-csv 粘贴 CSV 文本导入（merge 覆盖合并 / replace 全量替换）
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.fund import Fund
from backend.models.user_position import UserPosition
from backend.schemas.common import ApiResponse
from backend.services import position_service

logger = logging.getLogger(__name__)
router = APIRouter()


class PositionCreate(BaseModel):
    fund_code: str = Field(..., max_length=10)
    shares: float = Field(..., gt=0)
    cost_nav: Optional[float] = Field(None, gt=0)


class PositionUpdate(BaseModel):
    shares: Optional[float] = Field(None, gt=0)
    cost_nav: Optional[float] = Field(None, gt=0)


class PositionImportRequest(BaseModel):
    text: str = Field(..., max_length=500_000, description="CSV 原文（支付宝/天天基金导出兼容）")
    mode: str = Field("merge", pattern="^(merge|replace)$", description="merge=按代码覆盖合并；replace=清空后导入")


@router.get("", response_model=ApiResponse)
async def list_positions(db: AsyncSession = Depends(get_db)):
    items = await position_service.list_positions(db)
    return ApiResponse(data={"count": len(items), "items": items})


@router.post("", response_model=ApiResponse)
async def create_position(body: PositionCreate, db: AsyncSession = Depends(get_db)):
    try:
        result = await position_service.upsert_position(
            db, body.fund_code, body.shares, body.cost_nav, source="manual",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(data=result)


@router.put("/{position_id}", response_model=ApiResponse)
async def update_position(
    position_id: int, body: PositionUpdate, db: AsyncSession = Depends(get_db),
):
    p = (await db.execute(
        select(UserPosition).where(UserPosition.id == position_id)
    )).scalars().first()
    if not p:
        raise HTTPException(status_code=404, detail="持仓记录不存在")
    if body.shares is not None:
        p.shares = body.shares
    if body.cost_nav is not None:
        p.cost_nav = body.cost_nav
    await db.commit()
    return ApiResponse(data={"id": p.id})


@router.delete("/{position_id}", response_model=ApiResponse)
async def delete_position(position_id: int, db: AsyncSession = Depends(get_db)):
    p = (await db.execute(
        select(UserPosition).where(UserPosition.id == position_id)
    )).scalars().first()
    if not p:
        raise HTTPException(status_code=404, detail="持仓记录不存在")
    await db.delete(p)
    await db.commit()
    return ApiResponse(data={"deleted": position_id})


@router.post("/import-csv", response_model=ApiResponse)
async def import_csv(body: PositionImportRequest, db: AsyncSession = Depends(get_db)):
    rows, errors = position_service.parse_positions_csv(body.text)
    if not rows:
        return ApiResponse(
            code=1,
            data={"imported": 0, "updated": 0, "skipped": len(errors), "errors": errors},
            message="; ".join(errors[:5]) or "未解析到有效持仓行",
        )
    # 池外代码先行剔除，保证 skipped 计数准确
    codes = {r["code"] for r in rows}
    known = set((await db.execute(
        select(Fund.code).where(Fund.code.in_(codes))
    )).scalars().all())
    valid = [r for r in rows if r["code"] in known]
    for r in rows:
        if r["code"] not in known:
            errors.append(f"{r['code']}: 不在基金池，已跳过（请先在基金池添加）")
    if body.mode == "replace":
        await db.execute(delete(UserPosition))
        await db.commit()
    imported = updated = 0
    for r in valid:
        res = await position_service.upsert_position(
            db, r["code"], r["shares"], r["cost_nav"], source="import",
        )
        imported += 1 if res["created"] else 0
        updated += 0 if res["created"] else 1
    return ApiResponse(data={
        "imported": imported, "updated": updated,
        "skipped": len(errors), "errors": errors[:20],
    })
