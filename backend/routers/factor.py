"""因子 CRUD 路由"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.common import ApiResponse
from backend.schemas.factor import FactorCreate, FactorUpdate, FactorOut
from backend.services.factor_service import FactorService

router = APIRouter()


@router.get("", response_model=ApiResponse[list[FactorOut]])
async def list_factors(db: AsyncSession = Depends(get_db)):
    """获取因子列表"""
    svc = FactorService(db)
    factors = await svc.list_factors()
    total_weight = await svc.get_total_weight(status="active")

    results = []
    for f in factors:
        out = FactorOut.model_validate(f)
        # 计算 weight_percentage
        if f.params and isinstance(f.params, str):
            out.params = __import__("json").loads(f.params)
        else:
            out.params = f.params
        out.weight_percentage = round(f.weight / total_weight * 100, 2) if total_weight > 0 else 0.0
        results.append(out)

    return ApiResponse(data=results)


@router.post("", response_model=ApiResponse[FactorOut])
async def create_factor(
    body: FactorCreate,
    db: AsyncSession = Depends(get_db),
):
    """新增因子"""
    svc = FactorService(db)
    try:
        factor = await svc.create_factor(body)
        out = FactorOut.model_validate(factor)
        out.weight_percentage = 0.0  # 新增后需重新计算
        return ApiResponse(data=out)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{factor_id}", response_model=ApiResponse[FactorOut])
async def update_factor(
    factor_id: int,
    body: FactorUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新因子"""
    svc = FactorService(db)
    factor = await svc.update_factor(factor_id, body)
    if factor is None:
        raise HTTPException(status_code=404, detail="因子不存在")

    total_weight = await svc.get_total_weight(status="active")
    out = FactorOut.model_validate(factor)
    if factor.params and isinstance(factor.params, str):
        import json
        out.params = json.loads(factor.params)
    out.weight_percentage = round(factor.weight / total_weight * 100, 2) if total_weight > 0 else 0.0
    return ApiResponse(data=out)


@router.delete("/{factor_id}", response_model=ApiResponse[None])
async def delete_factor(
    factor_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除因子"""
    svc = FactorService(db)
    ok = await svc.delete_factor(factor_id)
    if not ok:
        raise HTTPException(status_code=404, detail="因子不存在")
    return ApiResponse()
