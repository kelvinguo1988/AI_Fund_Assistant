"""AI 对话 Pydantic Schema"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """AI 对话请求体"""
    content: str = Field(..., description="用户消息内容")
    conversation_id: Optional[str] = Field(None, description="新对话=None, 续聊传 ID")
    context_type: Optional[Literal['single_fund', 'pool', 'market']] = Field(
        None, description="上下文类型"
    )
    fund_id: Optional[int] = Field(None, description="关联基金 ID")


class ChatResponse(BaseModel):
    """AI 对话响应体"""
    model_config = {"protected_namespaces": ()}

    conversation_id: str
    role: str = "assistant"
    content: str
    model_name: str
