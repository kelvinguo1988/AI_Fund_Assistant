"""AI 对话业务逻辑"""

import json
import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.ai_conversation import AIConversation
from backend.models.fund import Fund
from backend.models.system_config import SystemConfig
from backend.models.analysis_result import AnalysisResult
from backend.schemas.ai import ChatMessage, ChatResponse

logger = logging.getLogger(__name__)


class AIService:
    """AI 对话服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def chat(self, message: ChatMessage) -> ChatResponse:
        """处理 AI 对话请求

        Args:
            message: 用户消息

        Returns:
            ChatResponse AI 回复

        Raises:
            ValueError: AI 功能未启用
        """
        # 检查 AI 是否启用
        config_map = await self._get_config_map()
        ai_enabled = config_map.get("ai_enabled", "true").lower() == "true"
        if not ai_enabled:
            raise ValueError("AI 功能未启用")

        ai_model = config_map.get("ai_model", "deepseek")
        ai_api_key = config_map.get("ai_api_key", "")
        ai_base_url = config_map.get("ai_base_url", "https://api.deepseek.com/v1")

        if not ai_api_key:
            raise ValueError("AI API Key 未配置")

        # 获取或创建会话 ID
        conversation_id = message.conversation_id or str(uuid.uuid4())

        # 构建系统提示词
        system_prompt = await self._build_system_prompt(message)

        # 获取历史对话
        history = await self._get_conversation_history(conversation_id)
        messages = [
            {"role": msg.role, "content": msg.content}
            for msg in history[-10:]  # 保留最近 10 轮
        ]
        messages.append({"role": "user", "content": message.content})

        # 调用 LLM
        from backend.llm.factory import LLMFactory
        provider = LLMFactory.create(ai_model, ai_api_key, ai_base_url)

        try:
            ai_response = await provider.chat(
                system_prompt=system_prompt,
                messages=messages,
            )
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise ValueError(f"AI 服务调用失败: {str(e)}")

        # 存储用户消息
        user_conv = AIConversation(
            conversation_id=conversation_id,
            role="user",
            content=message.content,
            context_type=message.context_type,
            fund_id=message.fund_id,
            created_at=__import__("datetime").datetime.now(),
        )
        self.db.add(user_conv)

        # 存储 AI 回复
        assistant_conv = AIConversation(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
            context_type=message.context_type,
            fund_id=message.fund_id,
            model_name=ai_model,
            created_at=__import__("datetime").datetime.now(),
        )
        self.db.add(assistant_conv)
        await self.db.commit()

        return ChatResponse(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
            model_name=ai_model,
        )

    async def _build_system_prompt(self, message: ChatMessage) -> str:
        """构建系统提示词"""
        base_prompt = (
            "你是基金量化交易系统的AI助手，专门帮助用户分析基金投资机会。"
            "你可以基于量化因子评分、市场数据和历史分析结果，为用户提供专业的投资建议。\n\n"
            "注意事项：\n"
            "- 所有建议仅供参考，不构成投资建议\n"
            "- 使用红涨绿跌的中国市场惯例\n"
            "- 回答要简洁专业，数据驱动\n"
        )

        # 根据上下文类型补充信息
        context_parts: list[str] = []

        if message.context_type == "single_fund" and message.fund_id:
            # 单基金上下文
            fund_result = await self.db.execute(select(Fund).where(Fund.id == message.fund_id))
            fund = fund_result.scalars().first()
            if fund:
                context_parts.append(f"\n当前分析基金: {fund.name}({fund.code})")

                # 获取最新分析结果
                analysis_result = await self.db.execute(
                    select(AnalysisResult)
                    .where(AnalysisResult.fund_id == fund.id)
                    .order_by(AnalysisResult.analysis_date.desc())
                    .limit(1)
                )
                analysis = analysis_result.scalars().first()
                if analysis:
                    context_parts.append(f"最新评分: {analysis.weighted_score}")
                    context_parts.append(f"信号方向: {analysis.signal_direction}")
                    context_parts.append(f"信号强度: {analysis.signal_strength}")
                    context_parts.append(f"操作建议: {analysis.operation_advice}")

                    try:
                        scores = json.loads(analysis.factor_scores)
                        context_parts.append(f"因子评分: {json.dumps(scores, ensure_ascii=False)}")
                    except (json.JSONDecodeError, TypeError):
                        pass

        elif message.context_type == "pool":
            # 基金池上下文
            funds_result = await self.db.execute(select(Fund).where(Fund.status == "active"))
            funds = funds_result.scalars().all()
            if funds:
                fund_names = ", ".join(f"{f.name}({f.code})" for f in funds)
                context_parts.append(f"\n当前基金池: {fund_names}")

        elif message.context_type == "market":
            context_parts.append("\n用户询问市场行情相关内容")

        if context_parts:
            base_prompt += "\n当前上下文信息:\n" + "\n".join(context_parts)

        return base_prompt

    async def _get_conversation_history(self, conversation_id: str) -> list[AIConversation]:
        """获取对话历史"""
        result = await self.db.execute(
            select(AIConversation)
            .where(AIConversation.conversation_id == conversation_id)
            .order_by(AIConversation.created_at)
        )
        return list(result.scalars().all())

    async def _get_config_map(self) -> dict[str, str]:
        """获取系统配置 KV 映射"""
        result = await self.db.execute(select(SystemConfig))
        configs = result.scalars().all()
        return {c.config_key: c.config_value for c in configs}
