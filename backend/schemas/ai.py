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


class AISkillBase(BaseModel):
    """AI Skill 基础字段"""
    name: str = Field(..., max_length=100, description="Skill 名称（唯一）")
    description: Optional[str] = Field(None, max_length=500, description="功能描述")
    system_prompt: str = Field(..., max_length=50_000, description="系统提示词（支持 {{fund_pool}}/{{market_regime}}/{{fund:<id>}} 占位符）")
    enabled: bool = Field(True, description="是否启用")


class AISkillOut(AISkillBase):
    id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AISkillCreate(AISkillBase):
    pass


class AISkillUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    system_prompt: Optional[str] = None
    enabled: Optional[bool] = None


class AISkillImportItem(AISkillBase):
    """批量导入条目（按 name upsert）"""
    pass


class AISkillImportResult(BaseModel):
    """批量导入结果"""
    created: int = 0
    updated: int = 0
    errors: list[str] = []


class AISkillToggle(BaseModel):
    enabled: bool
