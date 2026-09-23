"""LLM 抽象基类

chat()          既有纯文本对话（各 Provider 实现，保持向后兼容）
chat_with_tools / astream_with_tools
                OpenAI 兼容 function-calling 与流式（2026-09-23 AI Agent 重构）——
                四家 Provider 均为 AsyncOpenAI 兼容接口，默认实现下沉到基类，
                子类只需提供 self._client；模型不支持 tools 时服务端报错，
                agent_runner 捕获后可回退无工具模式。
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """模型请求执行的一次工具调用"""
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    """chat_with_tools / astream 的统一返回"""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _parse_completion(response) -> LLMResponse:
    msg = response.choices[0].message
    tool_calls: list[ToolCall] = []
    for tc in (msg.tool_calls or []):
        try:
            args = json.loads(tc.function.arguments or "{}")
            if not isinstance(args, dict):
                args = {"__raw__": args}
        except (json.JSONDecodeError, TypeError):
            # 模型输出残缺 JSON：以空参执行并记录，避免整轮崩溃
            logger.warning(f"tool_call 参数 JSON 解析失败 name={tc.function.name}: {tc.function.arguments!r}")
            args = {}
        tool_calls.append(ToolCall(id=tc.id or "", name=tc.function.name or "", arguments=args))
    usage = getattr(response, "usage", None)
    return LLMResponse(
        content=(msg.content or ""),
        tool_calls=tool_calls,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类"""

    def __init__(self, model_name: str, api_key: str, base_url: str) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url

    @abstractmethod
    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        """发送对话请求（纯文本，向后兼容路径）

        Args:
            system_prompt: 系统提示词
            messages: 对话历史 [{"role": "user"/"assistant", "content": "..."}]
            max_tokens: 最大生成 token 数
            temperature: 生成温度

        Returns:
            AI 回复文本
        """
        ...

    # ── Agent 能力（OpenAI 兼容 tools/stream），基于子类自建的 self._client ──

    def _require_client(self):
        client = getattr(self, "_client", None)
        if client is None:
            raise NotImplementedError(f"{type(self).__name__} 未提供 AsyncOpenAI 兼容 _client，无法使用 tool-calling")
        return client

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        """单轮 function-calling 调用；messages 需自带 system 消息"""
        kwargs: dict = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = await self._require_client().chat.completions.create(**kwargs)
        return _parse_completion(response)

    async def astream_with_tools(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> AsyncIterator[dict]:
        """流式 function-calling。

        产出事件：
          {"type": "delta", "text": ...}      正文增量（无工具调用的轮次即最终回答流）
          {"type": "tool_call", "name", "arguments"}   某个工具调用参数已完整
          {"type": "final", "response": LLMResponse}   终态（一定产出且仅一次）
        工具调用轮通常无正文增量；调用方据 final.tool_calls 决定是否回填结果后继续。
        """
        kwargs: dict = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        stream = await self._require_client().chat.completions.create(**kwargs)

        content_parts: list[str] = []
        tool_acc: dict[int, dict] = {}  # index -> {id, name, arguments 拼接}
        prompt_tokens = completion_tokens = 0
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                prompt_tokens = chunk.usage.prompt_tokens or 0
                completion_tokens = chunk.usage.completion_tokens or 0
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                yield {"type": "delta", "text": delta.content}
            for tc in (delta.tool_calls or []):
                slot = tool_acc.setdefault(tc.index or 0, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

        tool_calls: list[ToolCall] = []
        for idx in sorted(tool_acc):
            slot = tool_acc[idx]
            try:
                args = json.loads(slot["arguments"] or "{}")
                if not isinstance(args, dict):
                    args = {"__raw__": args}
            except (json.JSONDecodeError, TypeError):
                args = {}
            tc = ToolCall(id=slot["id"], name=slot["name"], arguments=args)
            tool_calls.append(tc)
            yield {"type": "tool_call", "name": tc.name, "arguments": tc.arguments}

        yield {
            "type": "final",
            "response": LLMResponse(
                content="".join(content_parts),
                tool_calls=tool_calls,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
        }
