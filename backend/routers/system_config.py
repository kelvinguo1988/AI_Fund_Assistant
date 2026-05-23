"""系统配置路由"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.system_config import SystemConfig
from backend.schemas.common import ApiResponse
from backend.schemas.system_config import (
    AIConfigUpdate,
    AIConfigOut,
    ConnectivityResult,
    ScoringConfigOut,
    ScoringConfigUpdate,
    ScoringTier,
)
from backend.services.connectivity_service import test_all_connectivity
from backend.engines.scoring_engine import DEFAULT_THRESHOLDS

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


def _default_thresholds_to_schema() -> list[ScoringTier]:
    """将引擎默认阈值转为 Pydantic schema 列表"""
    tiers = []
    for t in DEFAULT_THRESHOLDS:
        tiers.append(ScoringTier(
            min_score=t["min_score"],
            label=t["label"],
            signal_direction=t["signal_direction"],
            signal_strength=t["signal_strength"],
            operation_advice=t["operation_advice"],
            equity_ratio=t["equity_ratio"],
        ))
    return tiers


@router.get("/scoring-config", response_model=ApiResponse[ScoringConfigOut])
async def get_scoring_config(db: AsyncSession = Depends(get_db)):
    """获取评分阈值配置

    自动迁移：若存储的配置不足 5 档（旧版），追加兜底档位（强烈减仓）。
    """
    config_map = await _get_config_map(db)
    raw = config_map.get("scoring_thresholds", "")

    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                thresholds = [ScoringTier(**t) for t in data]
                # 自动迁移：确保末档为 catch-all（min_score 等于理论最小值 -6.4）
                # 避免重复追加：仅当末档不是 heavy_sell 时才追加
                last = thresholds[-1]
                if last.min_score > -6.4 and last.signal_strength != "heavy_sell":
                    catch_all = DEFAULT_THRESHOLDS[-1]
                    thresholds.append(ScoringTier(
                        min_score=-6.4,
                        label=catch_all["label"],
                        signal_direction=catch_all["signal_direction"],
                        signal_strength=catch_all["signal_strength"],
                        operation_advice=catch_all["operation_advice"],
                        equity_ratio=catch_all["equity_ratio"],
                    ))
                    # 持久化迁移后的配置
                    raw = json.dumps([t.model_dump() for t in thresholds], ensure_ascii=False)
                    result = await db.execute(
                        select(SystemConfig).where(SystemConfig.config_key == "scoring_thresholds")
                    )
                    config = result.scalars().first()
                    if config:
                        config.config_value = raw
                    else:
                        db.add(SystemConfig(config_key="scoring_thresholds", config_value=raw))
                    await db.commit()
                    logger.info("评分配置自动迁移：追加末档 catch-all（min_score=-6.4）")
                return ApiResponse(data=ScoringConfigOut(thresholds=thresholds))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("评分阈值配置解析失败，使用默认值: %s", e)

    # 使用默认值
    return ApiResponse(data=ScoringConfigOut(thresholds=_default_thresholds_to_schema()))


@router.put("/scoring-config", response_model=ApiResponse[ScoringConfigOut])
async def update_scoring_config(
    body: ScoringConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新评分阈值配置"""
    # 验证：至少需要 3 个档位，且按 min_score 降序
    tiers = body.thresholds
    if len(tiers) < 3:
        raise HTTPException(status_code=400, detail="至少需要 3 个档位")

    # 检查降序
    for i in range(len(tiers) - 1):
        if tiers[i].min_score <= tiers[i + 1].min_score:
            raise HTTPException(
                status_code=400,
                detail="档位必须按 min_score 降序排列",
            )

    # 序列化并保存
    raw = json.dumps([t.model_dump() for t in tiers], ensure_ascii=False)

    result = await db.execute(
        select(SystemConfig).where(SystemConfig.config_key == "scoring_thresholds")
    )
    config = result.scalars().first()
    if config:
        config.config_value = raw
    else:
        db.add(SystemConfig(config_key="scoring_thresholds", config_value=raw))

    await db.commit()

    return ApiResponse(data=ScoringConfigOut(thresholds=tiers))


@router.get("/connectivity", response_model=ApiResponse[ConnectivityResult])
async def test_connectivity(
    db: AsyncSession = Depends(get_db),
):
    """测试所有数据源的连通性（东方财富系列域名 + AI API）"""
    config_map = await _get_config_map(db)
    ai_enabled = config_map.get("ai_enabled", "true").lower() == "true"
    ai_base_url = config_map.get("ai_base_url", "")

    try:
        result = await test_all_connectivity(
            ai_base_url=ai_base_url if ai_enabled else "",
            ai_enabled=ai_enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"AI API 配置无效: {e}")
    return ApiResponse(data=result)
