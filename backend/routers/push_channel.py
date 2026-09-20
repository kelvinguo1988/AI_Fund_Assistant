"""推送渠道 CRUD 路由"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.push_channel import PushChannel
from backend.schemas.common import ApiResponse
from backend.schemas.push_channel import PushChannelCreate, PushChannelUpdate, PushChannelOut

logger = logging.getLogger(__name__)
router = APIRouter()


def _to_out(ch: PushChannel) -> PushChannelOut:
    """ORM → Schema 转换"""
    config = None
    if ch.config:
        try:
            config = json.loads(ch.config) if isinstance(ch.config, str) else ch.config
        except json.JSONDecodeError:
            config = None
    return PushChannelOut(
        id=ch.id,
        name=ch.name,
        channel_type=ch.channel_type,
        webhook_url=ch.webhook_url,
        token=ch.token,
        config=config,
        enabled=ch.enabled,
        created_at=ch.created_at,
        updated_at=ch.updated_at,
    )


@router.get("", response_model=ApiResponse[list[PushChannelOut]])
async def list_channels(db: AsyncSession = Depends(get_db)):
    """获取推送渠道列表"""
    result = await db.execute(select(PushChannel).order_by(PushChannel.id))
    channels = result.scalars().all()
    return ApiResponse(data=[_to_out(ch) for ch in channels])


@router.post("", response_model=ApiResponse[PushChannelOut])
async def create_channel(
    body: PushChannelCreate,
    db: AsyncSession = Depends(get_db),
):
    """新增推送渠道"""
    ch = PushChannel(
        name=body.name,
        channel_type=body.channel_type,
        webhook_url=body.webhook_url,
        token=body.token,
        config=json.dumps(body.config) if body.config else None,
        enabled=body.enabled,
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return ApiResponse(data=_to_out(ch))


@router.put("/{channel_id}", response_model=ApiResponse[PushChannelOut])
async def update_channel(
    channel_id: int,
    body: PushChannelUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新推送渠道"""
    result = await db.execute(select(PushChannel).where(PushChannel.id == channel_id))
    ch = result.scalars().first()
    if ch is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    update_data = body.model_dump(exclude_unset=True)
    if "config" in update_data and update_data["config"] is not None:
        update_data["config"] = json.dumps(update_data["config"])

    for key, value in update_data.items():
        setattr(ch, key, value)

    await db.commit()
    await db.refresh(ch)
    return ApiResponse(data=_to_out(ch))


@router.delete("/{channel_id}", response_model=ApiResponse[None])
async def delete_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除推送渠道"""
    result = await db.execute(select(PushChannel).where(PushChannel.id == channel_id))
    ch = result.scalars().first()
    if ch is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    await db.delete(ch)
    await db.commit()
    return ApiResponse()


@router.post("/{channel_id}/test", response_model=ApiResponse[None])
async def test_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """测试推送渠道"""
    result = await db.execute(select(PushChannel).where(PushChannel.id == channel_id))
    ch = result.scalars().first()
    if ch is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    try:
        from backend.push.feishu import FeishuPush
    except ImportError as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"推送模块不可用: {e}")

    if ch.channel_type != "feishu":
        # 不支持的类型必须先返回 400：放进 try 会被下方兜底 except 重包装成 500
        raise HTTPException(status_code=400, detail=f"暂不支持 {ch.channel_type} 类型测试")

    try:
        pusher = FeishuPush(webhook_url=ch.webhook_url or "", secret=ch.token)
        ok = await pusher.send_test()
    except Exception as e:
        logger.error(f"推送测试失败: {e}")
        raise HTTPException(status_code=500, detail="推送测试失败，详见服务端日志")

    if not ok:
        raise HTTPException(status_code=502, detail="渠道返回失败，请检查 Webhook 地址与签名密钥")
    return ApiResponse()
