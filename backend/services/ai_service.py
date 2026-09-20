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
from backend.utils.timezone import now_beijing

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

        # 获取历史对话（最近 10 轮 = 20 条，SQL LIMIT 取尾部，避免全量载入）
        history = await self._get_conversation_history(conversation_id, limit=20)
        messages = [
            {"role": msg.role, "content": msg.content}
            for msg in history
        ]
        messages.append({"role": "user", "content": message.content})

        # 调用 LLM（ai_model_id 非空时覆盖 preset 默认模型 ID，如 glm-4-plus）
        from backend.llm.factory import LLMFactory
        provider = LLMFactory.create(
            ai_model, ai_api_key, ai_base_url,
            model_id=config_map.get("ai_model_id") or None,
        )

        try:
            ai_response = await provider.chat(
                system_prompt=system_prompt,
                messages=messages,
            )
        except Exception as e:
            # 原始异常只进日志：detail 会经路由透给前端，防泄漏 API key/URL 等内部信息
            logger.error(f"LLM 调用失败: {type(e).__name__}: {e}", exc_info=True)
            raise ValueError("AI 服务调用失败")

        # 存储用户消息
        user_conv = AIConversation(
            conversation_id=conversation_id,
            role="user",
            content=message.content,
            context_type=message.context_type,
            fund_id=message.fund_id,
            created_at=now_beijing(),
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
            created_at=now_beijing(),
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
        """构建系统提示词 — 注入系统全量数据"""
        base_prompt = (
            "你是基金量化交易系统的AI助手，专门帮助用户分析基金投资机会。"
            "你可以基于量化因子评分、市场数据和历史分析结果，为用户提供专业的投资建议。\n\n"
            "注意事项：\n"
            "- 所有建议仅供参考，不构成投资建议\n"
            "- 使用红涨绿跌的中国市场惯例\n"
            "- 回答要简洁专业，数据驱动\n"
        )

        # ── 始终注入：系统全量数据 ──
        global_parts: list[str] = []

        # 1. 全部活跃基金 + 最新分析结果（JOIN 单查询取各基金最新一条，避免 N+1）
        funds_result = await self.db.execute(select(Fund).where(Fund.status == "active"))
        funds = funds_result.scalars().all()
        if funds:
            from sqlalchemy import func

            latest = (
                select(
                    AnalysisResult.fund_id,
                    func.max(AnalysisResult.analysis_date).label("max_date"),
                )
                .group_by(AnalysisResult.fund_id)
                .subquery()
            )
            ars_result = await self.db.execute(
                select(AnalysisResult).join(
                    latest,
                    (AnalysisResult.fund_id == latest.c.fund_id)
                    & (AnalysisResult.analysis_date == latest.c.max_date),
                )
            )
            analysis_by_fund: dict[int, AnalysisResult] = {}
            for a in ars_result.scalars():
                analysis_by_fund.setdefault(a.fund_id, a)

            fund_lines: list[str] = []
            for f in funds:
                analysis = analysis_by_fund.get(f.id)
                if analysis:
                    line = (
                        f"  {f.name}({f.code}): "
                        f"评分={analysis.weighted_score:.2f}, "
                        f"方向={analysis.signal_direction}, "
                        f"强度={analysis.signal_strength}"
                    )
                    try:
                        scores = json.loads(analysis.factor_scores)
                        # 只保留 |score| 最大的 5 个因子，控制 token 体积
                        if isinstance(scores, dict) and scores:
                            top = sorted(
                                scores.items(),
                                key=lambda kv: abs(float(kv[1] or 0)),
                                reverse=True,
                            )[:5]
                            line += ", 因子=" + ", ".join(f"{k}={v}" for k, v in top)
                        elif isinstance(scores, list) and scores:
                            top = sorted(
                                scores,
                                key=lambda fs: abs(float(fs.get("score", 0) or 0)),
                                reverse=True,
                            )[:5]
                            line += ", 因子=" + ", ".join(
                                f"{fs.get('factor_name', fs.get('factor_code', '?'))}={fs.get('score')}"
                                for fs in top
                            )
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                    fund_lines.append(line)
                else:
                    fund_lines.append(f"  {f.name}({f.code}): 暂无分析数据")
            pool_block = "\n【基金池及最新分析】\n" + "\n".join(fund_lines)
            # token 预算：大池截断，防止系统提示词膨胀挤占上下文
            if len(pool_block) > 4000:
                pool_block = pool_block[:4000] + "\n…（基金列表过长已截断）"
            global_parts.append(pool_block)

        # 2. 评分阈值配置
        config_map = await self._get_config_map()
        thresholds_raw = config_map.get("scoring_thresholds", "")
        if thresholds_raw:
            try:
                thresholds = json.loads(thresholds_raw)
                tier_lines = [
                    f"  最低分≥{t['min_score']}: {t['label']} ({t['signal_direction']}/{t['signal_strength']})"
                    for t in thresholds
                ]
                global_parts.append("\n【评分阈值配置】\n" + "\n".join(tier_lines))
            except (json.JSONDecodeError, TypeError):
                pass

        if global_parts:
            base_prompt += "\n系统当前数据:\n" + "\n".join(global_parts)

        # ── 根据上下文类型补充特定信息 ──
        context_parts: list[str] = []

        if message.context_type == "single_fund" and message.fund_id:
            fund_result = await self.db.execute(select(Fund).where(Fund.id == message.fund_id))
            fund = fund_result.scalars().first()
            if fund:
                context_parts.append(f"\n当前分析基金: {fund.name}({fund.code})")

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
            if funds:
                fund_names = ", ".join(f"{f.name}({f.code})" for f in funds)
                context_parts.append(f"\n当前基金池: {fund_names}")

        elif message.context_type == "market":
            context_parts.append("\n用户询问市场行情相关内容")

        if context_parts:
            base_prompt += "\n当前上下文信息:\n" + "\n".join(context_parts)

        # ── 启用的 Skill 注入（占位符渲染后置于数据上下文之前）──
        from backend.services.ai_skill_service import (
            build_skills_prompt, get_enabled_skills, render_skill_prompts,
        )
        skills = await get_enabled_skills(self.db)
        if skills:
            rendered = await render_skill_prompts(skills, self.db)
            skill_blocks = [
                f"### Skill: {name}\n{prompt}" for name, prompt in rendered
            ]
            base_prompt += "\n\n【启用的分析技能（必须遵循其中的分析框架与输出要求）】\n" + "\n\n".join(skill_blocks)

        return base_prompt

    async def _get_conversation_history(
        self, conversation_id: str, limit: int = 20
    ) -> list[AIConversation]:
        """获取对话历史（取最近 limit 条，按时间正序返回）"""
        result = await self.db.execute(
            select(AIConversation)
            .where(AIConversation.conversation_id == conversation_id)
            .order_by(AIConversation.created_at.desc(), AIConversation.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def _get_config_map(self) -> dict[str, str]:
        """获取系统配置 KV 映射"""
        result = await self.db.execute(select(SystemConfig))
        configs = result.scalars().all()
        return {c.config_key: c.config_value for c in configs}
