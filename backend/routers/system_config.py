"""系统配置路由"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.system_config import SystemConfig
from backend.schemas.common import ApiResponse
from backend.schemas.system_config import AIConfigUpdate, AIConfigOut

logger = logging.getLogger(__name__)
router = APIRouter()


async def _get_config_map(db: AsyncSession) -> dict[str, str]:
    """获取所有系统配置的 KV 映射"""
    result = await db.execute(select(SystemConfig))
    configs = result.scalars().all()
    return {c.config_key: c.config_value for c in configs}


@router.get("", response_model=ApiResponse[AIConfigOut])
async def get_system_config(db: AsyncSession = Depends(get_db)):
    """获取系统配置"""
    config_map = await _get_config_map(db)

    return ApiResponse(data=AIConfigOut(
        ai_enabled=config_map.get("ai_enabled", "true").lower() == "true",
        ai_model=config_map.get("ai_model", "deepseek"),
        ai_base_url=config_map.get("ai_base_url", "https://api.deepseek.com/v1"),
    ))


@router.put("", response_model=ApiResponse[AIConfigOut])
async def update_system_config(
    body: AIConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新系统配置"""
    update_map: dict[str, str] = {}

    if body.ai_enabled is not None:
        update_map["ai_enabled"] = str(body.ai_enabled).lower()
    if body.ai_model is not None:
        update_map["ai_model"] = body.ai_model
    if body.ai_api_key is not None:
        update_map["ai_api_key"] = body.ai_api_key
    if body.ai_base_url is not None:
        update_map["ai_base_url"] = body.ai_base_url

    for key, value in update_map.items():
        result = await db.execute(select(SystemConfig).where(SystemConfig.config_key == key))
        config = result.scalars().first()
        if config:
            config.config_value = value
        else:
            db.add(SystemConfig(config_key=key, config_value=value))

    await db.commit()

    # 重新获取
    config_map = await _get_config_map(db)
    return ApiResponse(data=AIConfigOut(
        ai_enabled=config_map.get("ai_enabled", "true").lower() == "true",
        ai_model=config_map.get("ai_model", "deepseek"),
        ai_base_url=config_map.get("ai_base_url", "https://api.deepseek.com/v1"),
    ))
