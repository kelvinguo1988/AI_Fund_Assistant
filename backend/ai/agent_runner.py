"""AI 分析 Agent 运行器（ReAct：LLM ⇄ 只读工具，流式事件）

设计（docs/AI_AGENT_PLAN.md §3.2）：
- 每轮 astream_with_tools：正文增量直出（最终回答即最后一轮流）；出现 tool_calls
  则发 delta_reset 丢弃该轮文本，执行工具回填 role=tool 后进入下一轮。
- 护栏：max_rounds、单轮 LLM 超时、token 预算；全部工具只读（registry 铁律）。
- 持久化：user 消息 + assistant 最终回答（agent_events 存工具轨迹摘要供回放）。
"""

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncIterator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ai_tools.registry import execute_tool, tool_specs
from backend.models.ai_conversation import AIConversation
from backend.models.system_config import SystemConfig
from backend.utils.timezone import now_beijing

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROUNDS = 8
DEFAULT_LLM_TIMEOUT = 90.0
DEFAULT_TOKEN_BUDGET = 120_000  # prompt+completion 累计上限，超出强制收敛

SYSTEM_PROMPT = """你是「AI 基金量化助手」的分析 Agent，服务于一个场外基金（含少量 ETF）量化系统。你通过调用只读工具获取真实数据，禁止编造任何数字。

# 系统口径（引用数据时必须遵守）
- 评分 weighted_score 范围 ±8.5（11 因子总权重 8.3，截面 z-score 五桶映射 ±1/±0.5/0）。
- 信号五档：≥4.0 强烈买入 / ≥1.5 买入（动态阈值随市场环境浮动，以工具返回的 dynamic_*_threshold 为准）/ -3.0~1.5 观望 / -3.0~-1.5 卖出 / <-3.0 强烈卖出（以配置为准，可用 get_scoring_config 核对）。
- 场外基金买入建议必须先用 get_otc_trade_status 核查申购可执行性（暂停申购/封闭期不得给买入建议）。
- 因子分 0 常见且合法：数据无效基金被剔除出截面池记 0；不要把它解读为"中性看平"。

# 工作纪律
- 回答中的每个数字都应能追溯到某次工具返回；结论要区分"数据显示"与"我的推断"。
- 优先小步查询（指定 code/days/limit），避免一次性拉全池明细。
- 最终回答用 Markdown：结论在前，表格列数据，末尾用一行注明本次调用了哪些工具。
- 用户问与基金无关的问题，直接说明系统边界，不要调用工具。"""


async def _load_ai_config(db: AsyncSession) -> dict:
    rows = (await db.execute(select(SystemConfig))).scalars().all()
    cm = {c.config_key: c.config_value for c in rows}
    return {
        "ai_enabled": cm.get("ai_enabled", "true").lower() == "true",
        "ai_model": cm.get("ai_model", "deepseek"),
        "ai_api_key": cm.get("ai_api_key", ""),
        "ai_base_url": cm.get("ai_base_url", "https://api.deepseek.com/v1"),
        "ai_model_id": (cm.get("ai_model_id") or "").strip() or None,
        "agent_enabled": (cm.get("ai_agent_enabled") or "true").lower() == "true",
    }


def _assistant_toolcall_msg(content: str, tool_calls) -> dict:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": tc.id or f"call_{i}",
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
            }
            for i, tc in enumerate(tool_calls)
        ],
    }


def _tool_msg(tc_id: str, payload: str) -> dict:
    return {"role": "tool", "tool_call_id": tc_id, "content": payload}


class AgentRunner:
    def __init__(
        self,
        db: AsyncSession,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        llm_timeout: float = DEFAULT_LLM_TIMEOUT,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        allowed_tools: Optional[list[str]] = None,
    ) -> None:
        self.db = db
        self.max_rounds = max_rounds
        self.llm_timeout = llm_timeout
        self.token_budget = token_budget
        self.allowed_tools = allowed_tools

    # ── LLM 流式单轮：逐事件 wait_for 超时，事件实时透传 ──

    async def _iter_round(self, agen) -> AsyncIterator[dict]:
        while True:
            try:
                ev = await asyncio.wait_for(agen.__anext__(), timeout=self.llm_timeout)
            except StopAsyncIteration:
                return
            yield ev

    async def run_stream(
        self,
        user_prompt: str,
        conversation_id: Optional[str] = None,
        extra_system: str = "",
        persist: bool = True,
    ) -> AsyncIterator[dict]:
        """执行一次 Agent 任务，yield 事件 dict（路由层序列化成 SSE）。"""
        cfg = await _load_ai_config(self.db)
        if not cfg["ai_enabled"] or not cfg["agent_enabled"]:
            yield {"type": "error", "message": "AI 或 Agent 功能未启用（系统设置中开启）"}
            return
        if not cfg["ai_api_key"]:
            yield {"type": "error", "message": "AI API Key 未配置"}
            return

        from backend.llm.factory import LLMFactory
        try:
            provider = LLMFactory.create(
                cfg["ai_model"], cfg["ai_api_key"], cfg["ai_base_url"],
                model_id=cfg["ai_model_id"],
            )
        except Exception as e:
            yield {"type": "error", "message": f"LLM 初始化失败: {type(e).__name__}"}
            return

        conversation_id = conversation_id or str(uuid.uuid4())
        # Skill-as-tool：本次运行专属工具，不进全局注册表；
        # 不受 allowed_tools 限制（guidance 类只读工具，预置任务同样可用）
        from backend.ai_tools.skill_tools import load_skill_tools
        try:
            skill_defs = await load_skill_tools(self.db)
        except Exception as e:
            logger.warning(f"Skill 工具加载失败（不影响内置工具）: {e}")
            skill_defs = []
        skill_map = {d.name: d for d in skill_defs}
        specs = tool_specs(self.allowed_tools, extra=skill_defs)

        history_rows = (await self.db.execute(
            select(AIConversation)
            .where(AIConversation.conversation_id == conversation_id)
            .order_by(AIConversation.created_at.desc(), AIConversation.id.desc())
            .limit(20)
        )).scalars().all()
        history = [{"role": r.role, "content": r.content} for r in reversed(history_rows)
                   if r.role in ("user", "assistant")]

        system = SYSTEM_PROMPT + (("\n" + extra_system) if extra_system else "")
        messages: list[dict] = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": user_prompt},
        ]

        yield {"type": "start", "conversation_id": conversation_id,
               "model": provider.model_name, "tools": len(specs)}

        events_trace: list[dict] = []
        token_spent = 0
        final_text = ""
        forced_summary = False
        errored = False

        for round_no in range(1, self.max_rounds + 1):
            yield {"type": "round", "round": round_no}
            resp = None
            try:
                agen = provider.astream_with_tools(messages, specs, temperature=0.3)
                async for ev in self._iter_round(agen):
                    if ev["type"] == "final":
                        resp = ev["response"]
                    elif ev["type"] == "delta" and not forced_summary:
                        yield ev
                    elif ev["type"] == "tool_call":
                        yield ev
            except asyncio.TimeoutError:
                errored = True
                yield {"type": "error", "message": f"LLM 响应超时（第 {round_no} 轮）"}
                break
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e)[:150]}"
                if "tool" in msg.lower():
                    msg += "（当前模型可能不支持函数调用，请换用支持 tools 的模型）"
                logger.error(f"Agent LLM 调用失败 round={round_no}: {msg}")
                errored = True
                yield {"type": "error", "message": msg}
                break

            if resp is None:
                errored = True
                yield {"type": "error", "message": "LLM 无终态返回"}
                break
            token_spent += (resp.prompt_tokens + resp.completion_tokens) or 1
            if not resp.has_tool_calls:
                final_text = resp.content
                break
            # 工具轮：该轮流出的零星文本作废
            yield {"type": "delta_reset"}
            messages.append(_assistant_toolcall_msg(resp.content, resp.tool_calls))
            for tc in resp.tool_calls:
                t0 = time.monotonic()
                payload = await execute_tool(self.db, tc.name, tc.arguments,
                                             overrides=skill_map)
                ms = int((time.monotonic() - t0) * 1000)
                ok = True
                try:
                    ok = json.loads(payload).get("ok", False)
                except json.JSONDecodeError:
                    pass
                trace = {"round": round_no, "tool": tc.name, "arguments": tc.arguments,
                         "ok": ok, "ms": ms, "chars": len(payload)}
                events_trace.append(trace)
                yield {"type": "tool_result", **trace}
                messages.append(_tool_msg(tc.id or "", payload))
            if token_spent > self.token_budget:
                # 预算耗尽：末轮强制无工具总结
                forced_summary = True
                messages.append({"role": "user", "content": "工具调用预算已用完，请基于已获得的信息直接给出最终结论，不要再请求工具。"})
                specs = []

        else:
            forced_summary = True
            messages.append({"role": "user", "content": f"已达最大轮次 {self.max_rounds}，请基于已获得的信息直接给出最终结论，不要再请求工具。"})

        if errored:
            return

        if not final_text and (forced_summary or events_trace):
            # 收尾轮：不带工具，把文本流给用户
            try:
                agen = provider.astream_with_tools(messages, None, temperature=0.3)
                async for ev in self._iter_round(agen):
                    if ev["type"] == "final":
                        resp2 = ev["response"]
                        final_text = resp2.content or final_text
                        token_spent += (resp2.prompt_tokens + resp2.completion_tokens) or 1
                    elif ev["type"] == "delta":
                        yield ev
                        if not final_text:
                            pass  # 文本经 delta 实时透传，final 事件带完整 content
            except Exception as e:
                logger.warning(f"Agent 收尾轮失败: {e}")

        if not final_text:
            yield {"type": "error", "message": "Agent 未产出最终回答"}
            return

        yield {"type": "final", "conversation_id": conversation_id,
               "content": final_text, "rounds": len({t['round'] for t in events_trace}) + 1,
               "tool_calls": len(events_trace), "tokens": token_spent}

        if persist:
            try:
                self.db.add(AIConversation(
                    conversation_id=conversation_id, role="user", content=user_prompt,
                    context_type="agent", created_at=now_beijing(),
                ))
                self.db.add(AIConversation(
                    conversation_id=conversation_id, role="assistant", content=final_text,
                    context_type="agent", model_name=provider.model_name,
                    agent_events=json.dumps(events_trace, ensure_ascii=False)[:60000] or None,
                    created_at=now_beijing(),
                ))
                await self.db.commit()
            except Exception as e:
                logger.error(f"Agent 会话持久化失败: {e}")
