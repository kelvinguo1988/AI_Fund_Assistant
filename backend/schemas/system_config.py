"""系统配置 Pydantic Schema"""

from typing import Optional

from pydantic import BaseModel, Field


class AIConfigUpdate(BaseModel):
    """系统配置更新请求体"""
    ai_enabled: Optional[bool] = Field(None, description="AI 功能总开关")
    ai_model: Optional[str] = Field(None, description="AI 模型：deepseek / openai / tongyi")
    ai_api_key: Optional[str] = Field(None, description="AI 模型 API Key")
    ai_base_url: Optional[str] = Field(None, description="AI 模型 API Base URL")


class AIConfigOut(BaseModel):
    """系统配置输出 Schema（不返回 api_key）"""
    ai_enabled: bool
    ai_model: str
    ai_base_url: str
