"""AI 分析 Agent 路由 — SSE 流式执行 + 工具清单

端点：
  POST /api/ai/agent/run       运行一次 Agent 任务（text/event-stream）
  GET  /api/ai/agent/tools     已注册工具清单（工作台展示/调试）
事件协议见 backend/ai/agent_runner.py（start/round/delta/delta_reset/tool_call/
tool_result/final/error），前端据 type 渲染过程时间线。
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class AgentRunRequest(BaseModel):
    prompt: str = Field("", max_length=4000)
    conversation_id: Optional[str] = None
    task: Optional[str] = Field(None, description="预置任务 id（factor_audit 等），空=自由对话")
    max_rounds: Optional[int] = Field(None, ge=1, le=15)
    params: dict = Field(default_factory=dict, description="预置任务参数，如 {days: 90}")


# 预置任务注册表在 backend/ai/presets.py（与调度器 ai_daily_brief 共用）
from backend.ai.presets import PRESET_TASKS as _PRESET_TASKS


@router.post("/run")
async def run_agent(
    body: AgentRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """运行 Agent 任务，SSE 流式返回过程与结果"""
    from backend.ai.agent_runner import AgentRunner
    import backend.ai_tools.data_tools  # noqa: F401  触发工具注册

    preset = _PRESET_TASKS.get(body.task or "")
    if body.task and preset is None:
        raise HTTPException(status_code=400, detail=f"未知预置任务: {body.task}")
    prompt = (body.prompt or "").strip() or (preset or {}).get("default_prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 与 task 至少提供一个")

    runner = AgentRunner(
        db,
        max_rounds=(body.max_rounds or (preset or {}).get("max_rounds") or 8),
        allowed_tools=(preset or {}).get("allowed_tools"),
    )

    async def _stream():
        try:
            extra_system = ""
            builder = (preset or {}).get("context_builder")
            if builder is not None:
                yield "data: " + json.dumps({"type": "context", "note": "正在运行 Python 分析引擎…"}) + "\n\n"
                try:
                    extra_system = await builder(db, body.params)
                except Exception as e:
                    logger.error(f"预置任务上下文构建失败 task={body.task}: {e}", exc_info=True)
                    yield "data: " + json.dumps({
                        "type": "error", "message": f"分析引擎失败: {type(e).__name__}",
                    }) + "\n\n"
                    return
            async for event in runner.run_stream(
                user_prompt=prompt,
                conversation_id=body.conversation_id,
                extra_system=extra_system,
            ):
                yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
            yield "data: " + json.dumps({"type": "done"}) + "\n\n"
        finally:
            await db.close()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/factor-audit")
async def factor_audit_direct(days: int = 90):
    """因子诊断的纯 Python 结果（不经 LLM，AI 关闭时也可用；前端表格/导出直接消费）"""
    from backend.database import async_session_factory
    from backend.ai.factor_audit import FactorAuditService

    async with async_session_factory() as session:
        report = await FactorAuditService(session).audit(days=max(min(days, 365), 15))
        return {"ok": True, "data": report.to_dict(), "summary_md": report.summary_md()}


@router.get("/rebalance")
async def rebalance_direct(window_days: int = 30):
    """调仓四清单的纯 Python 结果（不经 LLM；前端调仓页/工单导出直接消费）"""
    from backend.database import async_session_factory
    from backend.ai.rebalance import RebalanceService

    async with async_session_factory() as session:
        report = await RebalanceService(session).analyze(
            window_days=max(min(window_days, 250), 7),
        )
        return {"ok": True, "data": report.to_dict(), "summary_md": report.summary_md()}


@router.get("/conversations")
async def agent_conversations(limit: int = 30):
    """工作台会话列表：最近 agent 会话（按 conversation_id 分组，取每组末条摘要）"""
    from backend.database import async_session_factory
    from backend.models.ai_conversation import AIConversation
    from sqlalchemy import select

    limit = max(min(limit, 100), 1)
    async with async_session_factory() as session:
        rows = (await session.execute(
            select(AIConversation)
            .where(AIConversation.context_type == "agent")
            .order_by(AIConversation.created_at.desc(), AIConversation.id.desc())
            .limit(300)
        )).scalars().all()
    grouped: dict[str, dict] = {}
    for r in rows:  # desc 序：首见即该会话最新一条
        g = grouped.get(r.conversation_id)
        if g is None:
            grouped[r.conversation_id] = {
                "conversation_id": r.conversation_id,
                "last_role": r.role,
                "preview": (r.content or "")[:80],
                "last_at": str(r.created_at),
                "turns": 1,
            }
        else:
            g["turns"] += 1
    return {"ok": True, "items": list(grouped.values())[:limit]}


@router.get("/tools")
async def list_tools():
    """已注册工具：name/description/category（前端能力面板）"""
    import backend.ai_tools.data_tools  # noqa: F401
    from backend.ai_tools.registry import _REGISTRY
    return {
        "ok": True,
        "tools": [
            {"name": d.name, "description": d.description, "category": d.category}
            for d in sorted(_REGISTRY.values(), key=lambda x: x.name) if not d.hidden
        ],
    }
