"""AI 对话路由"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.ai_conversation import AIConversation
from backend.models.system_config import SystemConfig
from backend.schemas.ai import ChatMessage, ChatResponse
from backend.schemas.common import ApiResponse
from backend.services.ai_service import AIService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat", response_model=ApiResponse[ChatResponse])
async def chat(
    body: ChatMessage,
    db: AsyncSession = Depends(get_db),
):
    """AI 对话"""
    # 检查 AI 是否启用
    result = await db.execute(select(SystemConfig).where(SystemConfig.config_key == "ai_enabled"))
    config = result.scalars().first()
    if config and config.config_value.lower() != "true":
        raise HTTPException(status_code=403, detail="AI 功能未启用")

    svc = AIService(db)
    try:
        response = await svc.chat(body)
        return ApiResponse(data=response)
    except ValueError as e:
        if "未启用" in str(e):
            raise HTTPException(status_code=403, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"AI 对话异常: {e}")
        raise HTTPException(status_code=500, detail=f"AI 服务异常: {str(e)}")


@router.get("/conversations", response_model=ApiResponse[list[dict]])
async def get_conversations(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """获取对话历史"""
    result = await db.execute(
        select(AIConversation)
        .where(AIConversation.conversation_id == conversation_id)
        .order_by(AIConversation.created_at)
    )
    conversations = result.scalars().all()
    return ApiResponse(data=[
        {
            "id": c.id,
            "conversation_id": c.conversation_id,
            "role": c.role,
            "content": c.content,
            "context_type": c.context_type,
            "fund_id": c.fund_id,
            "model_name": c.model_name,
            "created_at": str(c.created_at),
        }
        for c in conversations
    ])
