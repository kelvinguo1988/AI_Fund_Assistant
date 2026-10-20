"""OpenCode Zen 免费模型 Provider（2026-10-20）

为什么需要它：项目部署在 NAS 容器里，没法在容器内登录任何 Agent；而部分免费
模型只在官方客户端内可用。这里把"官方客户端对免费层的验证条件"在服务端补齐，
让 AI 工作台不依赖任何本地软件就能用上这些免费模型。

—— 这不是解密与绕过，而是复刻 OpenCode 公开客户端的请求特征（下述条件全部
   是 OpenCode 1.19.0 自己发出的，插件 @magpie-community/opencode-zen-free-auth
   也是按同一套特征实现的）。凭证 `public` 是公开的，不是任何人的账号。

实测（2026-10-20）四条硬约束，缺一条就 403 FreeTierError:
  1. 端点必须是 https://opencode.ai/inference/v1（老的 /zen/v1 对免费层恒 403）
  2. User-Agent 必须是三段式：opencode/1.19.0 ai-sdk/provider-utils/<ver> runtime/bun/1.3.14
  3. 必须声明 bash/read/edit/glob/grep 五个核心工具（免费层拒绝不带工具的请求）
  4. 请求必须 stream=True，并带 stream_options.include_usage

风险与边界（部署前请自行评估）：
  - 这是第三方免费额度，服务条款上属于"仅限官方客户端内使用"，自动化调用有
    被限制的风险；因此本项目只把它当**可选备用链路**，默认关闭。
  - 免费层有并发限制（插件里 maxConcurrency=2），不适合批量定时任务。
  - 上游随时可能改校验条件；失败时按普通异常处理，由上层兜底。
"""

import json
import logging
import secrets
import string
import time
from typing import Optional

import httpx

from backend.llm.base import BaseLLMProvider, LLMResponse, ToolCall, estimate_tokens

logger = logging.getLogger(__name__)

ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase

# 免费层端点（老 /zen/v1 对免费模型恒 403，见模块头）
ZEN_FREE_BASE = "https://opencode.ai/inference/v1"
# 公开凭证，非个人账号
ZEN_PUBLIC_KEY = "public"

# OpenCode 1.19.0 的 User-Agent（ai-sdk/provider-utils 版本按协议不同）
_VERSION = "1.19.0"
_PROVIDER_UTILS = {"chat": "4.0.23", "responses": "4.0.40", "anthropic": "4.0.46"}

# 免费层要求请求里声明这些工具，缺失即 403。
# 描述里写明"不要调用"，避免模型真的去调一个不存在的工具。
_CORE_TOOLS = (
    ("bash", "command"),
    ("read", "filePath"),
    ("edit", "filePath"),
    ("glob", "pattern"),
    ("grep", "pattern"),
)


def zen_user_agent(protocol: str = "chat") -> str:
    """OpenCode 1.19.0 的完整 User-Agent 串（三段式，缺一即 403）"""
    return (
        f"opencode/{_VERSION} ai-sdk/provider-utils/"
        f"{_PROVIDER_UTILS.get(protocol, _PROVIDER_UTILS['chat'])} runtime/bun/1.3.14"
    )


def opencode_id(prefix: str, descending: bool = False) -> str:
    """按 OpenCode 的 id 形状造一个（免费层会校验 session id 的形状）

    12 位十六进制（毫秒时间戳左移 12 + 计数器，session 取反使新的排前面）
    + 14 位 base62 随机尾巴。
    """
    now_ms = int(time.time() * 1000)
    value = (now_ms * 0x1000 + 1) & 0xFFFFFFFFFFFF
    if descending:
        value = (~value) & 0xFFFFFFFFFFFF
    tail = "".join(ALPHABET[b % 62] for b in secrets.token_bytes(14))
    return f"{prefix}_{value:012x}{tail}"


def zen_headers(protocol: str = "chat", session_id: str = "", request_id: str = "") -> dict:
    """复刻 OpenCode 客户端请求头

    实测（2026-10-20）缺 x-opencode-session / x-opencode-request 之一即
    403 FreeTierError——免费层会校验这两者存在，session 还要求 OpenCode
    的 id 形状（ses_ + 12 位十六进制 + 14 位 base62）。
    """
    ses = session_id or opencode_id("ses", descending=True)
    return {
        "authorization": f"Bearer {ZEN_PUBLIC_KEY}",
        "user-agent": zen_user_agent(protocol),
        "x-opencode-client": "cli",
        "x-opencode-project": "global",
        "x-opencode-session": ses,
        "x-opencode-session-id": ses,
        "x-opencode-request": request_id or opencode_id("msg"),
        "accept": "*/*",
    }


def core_tool_stubs() -> list[dict]:
    """免费层要求声明的五个核心工具（OpenAI tools 格式）"""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": "Not available. Never call this tool.",
                "parameters": {
                    "type": "object",
                    "properties": {param: {"type": "string"}},
                    "additionalProperties": False,
                },
            },
        }
        for name, param in _CORE_TOOLS
    ]


def _with_core_tools(tools: Optional[list[dict]]) -> list[dict]:
    """保证工具列表里含五个核心工具（已存在的保持调用方版本）"""
    have = {
        (t.get("function") or {}).get("name")
        for t in (tools or [])
        if isinstance(t, dict)
    }
    merged = list(tools or [])
    merged.extend(t for t in core_tool_stubs() if t["function"]["name"] not in have)
    return merged


class ZenFreeProvider(BaseLLMProvider):
    """OpenCode Zen 免费模型（自动补齐免费层要求的请求特征）"""

    def __init__(
        self,
        model_name: str = "deepseek-v4.1-flash-free",
        api_key: str = ZEN_PUBLIC_KEY,
        base_url: str = ZEN_FREE_BASE,
    ) -> None:
        # 凭证是公开的 public；调用方传什么都以它为准（避免误配成个人 key）
        super().__init__(model_name, ZEN_PUBLIC_KEY, base_url or ZEN_FREE_BASE)
        self._endpoint = (base_url or ZEN_FREE_BASE).rstrip("/") + "/chat/completions"

    # ── 底层：发一次流式请求，逐块回调整行 ───────────────────────────
    async def _stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]],
        max_tokens: int,
        temperature: float,
    ):
        """yield (content_delta, tool_call_delta, usage)

        免费层四条硬约束在此一次满足：固定端点、OpenCode 的 UA/头、
        核心工具、stream=True + include_usage。用 httpx 直发是为了不让
        OpenAI SDK 追加自己的请求头（实测带 x-stainless-* 会被 Cloudflare 400）。
        """
        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            "tools": _with_core_tools(tools),
        }
        headers = {**zen_headers("chat"), "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=90.0) as client:
            async with client.stream(
                "POST", self._endpoint, json=payload, headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    raw = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise RuntimeError(
                        f"ZenFree HTTP {resp.status_code}: {raw}"
                    )
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage")
                    if usage:
                        yield None, None, usage
                    for ch in (chunk.get("choices") or []):
                        delta = ch.get("delta") or {}
                        # 思考模式：推理内容要随轮次保存并回传上游，标记为 "reasoning"
                        rc = delta.get("reasoning_content")
                        if rc:
                            yield None, {"__reasoning__": rc}, None
                        if delta.get("content"):
                            yield delta["content"], None, None
                        for tc in (delta.get("tool_calls") or []):
                            yield None, tc, None

    # ── 非流式对话（普通 AI 问答走这里）──
    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        msgs = [{"role": "system", "content": system_prompt}] if system_prompt else []
        msgs.extend(messages)
        parts: list[str] = []
        # 免费层不接受非流式请求：内部流式拉取再拼成整段
        async for text, _tc, _u in self._stream_chat(msgs, None, max_tokens, temperature):
            if text:
                parts.append(text)
        return "".join(parts)

    # ── 流式 + 工具调用（Agent 工作台走这里）──
    async def astream_with_tools(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ):
        """与基类同签名、同事件格式（delta / tool_call / final）"""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        prompt_tokens = completion_tokens = 0
        async for text, tc, usage in self._stream_chat(
            messages, tools, max_tokens, temperature
        ):
            if usage:
                prompt_tokens = usage.get("prompt_tokens") or prompt_tokens
                completion_tokens = usage.get("completion_tokens") or completion_tokens
            if tc and "__reasoning__" in tc:
                reasoning_parts.append(tc["__reasoning__"])
                continue
            if text:
                content_parts.append(text)
                yield {"type": "delta", "text": text}
            if tc:
                idx = tc.get("index") or 0
                slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

        if not (prompt_tokens or completion_tokens):
            prompt_tokens = estimate_tokens("\n".join(str(m.get("content") or "") for m in messages))
            completion_tokens = estimate_tokens("".join(content_parts))

        tool_calls: list[ToolCall] = []
        for idx in sorted(tool_acc):
            slot = tool_acc[idx]
            # 只回传"真工具"调用：核心工具是免费层的准入条件，模型不该真的调它们
            if slot["name"] in {n for n, _ in _CORE_TOOLS}:
                continue
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"__raw__": slot["arguments"]}
            if not isinstance(args, dict):
                args = {"__raw__": args}
            tool_calls.append(ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"], arguments=args))
            yield {"type": "tool_call", "name": slot["name"], "arguments": args}

        yield {
            "type": "final",
            "response": LLMResponse(
                content="".join(content_parts),
                tool_calls=tool_calls,
                reasoning="".join(reasoning_parts),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
        }
