"""Skill-as-tool — 把配置了 tool_spec 的启用 Skill 动态注册为 Agent 工具

设计（docs/AI_AGENT_PLAN.md §3.7）：
- ai_skills.tool_spec 存 JSON：{"description": "...", "parameters": {JSON Schema}}
- 填了 tool_spec 的启用 skill → 生成本次运行专属 ToolDef（skill_<id>），
  不写全局注册表（避免并发运行互相污染）；agent_runner 经
  tool_specs(extra=...) / execute_tool(overrides=...) 消费。
- 工具被调用时返回渲染占位符后的 skill 指导文本（只读，不落库），
  相当于让模型按需拉取一段领域专家指令，比全量注入省上下文。
- tool_spec 非法 JSON / 缺 description → 跳过该 skill 并记 warning，
  不影响其余 skill 与提示词注入模式（完全向后兼容）。
"""

import json
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.ai_tools.registry import ToolDef

logger = logging.getLogger(__name__)


def _make_handler(skill_id: int):
    async def _handler(db: AsyncSession, **kwargs) -> dict:
        from backend.models.ai_skill import AISkill
        from backend.services.ai_skill_service import render_skill_prompts

        skill = await db.get(AISkill, skill_id)
        if skill is None or not skill.enabled:
            return {"skill": skill_id, "error": "Skill 不存在或已停用"}
        rendered = await render_skill_prompts([skill], db)
        return {"skill": skill.name, "guidance": rendered[0][1] if rendered else ""}
    return _handler


def parse_tool_spec(raw: Optional[str]) -> Optional[dict]:
    """解析 tool_spec JSON → {description, parameters}；非法返回 None"""
    if not raw or not raw.strip():
        return None
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(spec, dict):
        return None
    description = str(spec.get("description") or "").strip()
    if not description:
        return None
    parameters = spec.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        parameters = {"type": "object", "properties": {}}
    return {"description": description, "parameters": parameters}


async def load_skill_tools(db: AsyncSession) -> list[ToolDef]:
    """启用且 tool_spec 合法的 skill → 本次运行专属 ToolDef 列表"""
    from sqlalchemy import select

    from backend.models.ai_skill import AISkill

    rows = (await db.execute(
        select(AISkill).where(AISkill.enabled == True)  # noqa: E712
        .order_by(AISkill.id)
    )).scalars().all()

    defs: list[ToolDef] = []
    for sk in rows:
        spec = parse_tool_spec(sk.tool_spec)
        if spec is None:
            if sk.tool_spec and sk.tool_spec.strip():
                logger.warning(f"Skill tool_spec 非法，跳过注册: {sk.name}(id={sk.id})")
            continue
        name = f"skill_{sk.id}"
        defs.append(ToolDef(
            name=name,
            description=f"[Skill: {sk.name}] {spec['description']}",
            parameters=spec["parameters"],
            handler=_make_handler(sk.id),
            category="skill",
        ))
    return defs
