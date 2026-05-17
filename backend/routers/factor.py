"""因子 CRUD 路由"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.common import ApiResponse
from backend.schemas.factor import FactorCreate, FactorUpdate, FactorOut
from backend.services.factor_service import FactorService

router = APIRouter()


def _enrich_factor_out(f: "Factor", total_weight: float) -> FactorOut:
    """ORM → Schema 转换，处理 JSON 字段和 weight_percentage"""
    out = FactorOut.model_validate(f)
    # 解析 JSON 字符串字段
    json_fields = {
        "params": f.params,
        "data_fields": f.data_fields,
        "signal_rules": f.signal_rules,
        "normalization_config": f.normalization_config,
    }
    for field, raw in json_fields.items():
        if raw and isinstance(raw, str):
            try:
                setattr(out, field, json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                pass
    out.weight_percentage = round(f.weight / total_weight * 100, 2) if total_weight > 0 else 0.0
    return out


@router.get("", response_model=ApiResponse[list[FactorOut]])
async def list_factors(db: AsyncSession = Depends(get_db)):
    """获取因子列表"""
    svc = FactorService(db)
    factors = await svc.list_factors()
    total_weight = await svc.get_total_weight(status="active")
    results = [_enrich_factor_out(f, total_weight) for f in factors]
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
        total_weight = await svc.get_total_weight(status="active")
        out = _enrich_factor_out(factor, total_weight)
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
    out = _enrich_factor_out(factor, total_weight)
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
