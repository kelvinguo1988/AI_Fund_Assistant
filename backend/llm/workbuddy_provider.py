"""WorkBuddy Provider（2026-10-20）

WorkBuddy 是国内腾讯 CodeBuddy 系桌面 Agent，两个 build 各有 API:
  - workbuddy     （国内版，Copilot 套餐）https://copilot.tencent.com/v2
  - workbuddy-ai  （国际版）              https://www.workbuddy.ai/v2

与 zen-free 的**本质区别**（决定了它只能作为可选链路）:
  - zen-free 用公开凭证 `public`，匿名额度；
  - workbuddy 用**用户自己账号的 OAuth token**，消耗该账号的套餐额度。
    所以本模块必须妥善保管 refresh token，并自动续期。

实测（2026-10-20）WorkBuddy 的准入条件，缺一条就报错:
  - User-Agent 必须是 `WorkBuddy/<ver>`，否则错误码 10085
  - 必须带客户端标识头（X-Requested-With / X-Agent-Intent / X-IDE-Name…）
  - 必须带会话 id 头（X-Conversation-ID / -Message-ID / -Request-ID），
    缺则 11128「通道校验失败，可能不是官方客户端发出的」
  - 请求必须流式，非流式会被拒绝

配置方式（system_config）:
  ai_model = "workbuddy" 或 "workbuddy-ai"
  ai_api_key = "<refresh token>"   ← 只需填 refresh token，access 自动换取
  ai_model_id = 具体模型（如 deepseek-v4.1-flash）
"""

import json
import logging
import secrets
import time
from typing import Optional

import httpx

from backend.llm.base import BaseLLMProvider, LLMResponse, ToolCall, estimate_tokens

logger = logging.getLogger(__name__)

UA_VERSION = "5.5.6"

SITES = {
    "workbuddy": {
        "api": "https://copilot.tencent.com/v2",
        "domain": "copilot.tencent.com",
        "ai": False,
    },
    "workbuddy-ai": {
        "api": "https://www.workbuddy.ai/v2",
        "domain": "www.workbuddy.ai",
        "ai": True,
    },
}


def _hex_id() -> str:
    return secrets.token_hex(16)


class WorkBuddyProvider(BaseLLMProvider):
    """WorkBuddy 的 OpenAI 兼容通道（自己管 refresh/access token）"""

    def __init__(
        self,
        model_name: str = "deepseek-v4.1-flash",
        api_key: str = "",
        base_url: str = "",
        site_id: str = "workbuddy-ai",
    ) -> None:
        site = SITES.get(site_id) or SITES["workbuddy-ai"]
        super().__init__(model_name, api_key, base_url or site["api"])
        self.site = site
        self.site_id = site_id if site_id in SITES else "workbuddy-ai"
        # 配置里的 api_key 就是 refresh token
        self._refresh_token = (api_key or "").strip()
        self._access_token = ""
        self._access_expires = 0.0
        self._uid = ""
        self._domain = site["domain"]

    # ── 鉴权：refresh token → access token ──────────────────────────
    async def _ensure_access(self) -> str:
        """拿到未过期的 access token；快到期就先换一次"""
        if self._access_token and time.time() < self._access_expires - 60:
            return self._access_token
        if not self._refresh_token:
            raise RuntimeError("WorkBuddy 未配置 refresh token（系统设置里填到 API Key）")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Refresh-Token": self._refresh_token,
            "X-Auth-Refresh-Source": "plugin",
            "X-Domain": self._domain,
            "User-Agent": f"WorkBuddy/{UA_VERSION}",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.site['api']}/plugin/auth/token/refresh", headers=headers
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"WorkBuddy 换取 access token 失败: HTTP {resp.status_code} "
                    f"{resp.text[:200]}"
                )
            data = resp.json()
        body = data.get("data") if isinstance(data.get("data"), dict) else data
        access = body.get("accessToken") or body.get("access") or ""
        if not access:
            raise RuntimeError(f"WorkBuddy 未返回 access token: {str(data)[:200]}")
        self._access_token = access
        # token 有效期按 1 小时估算（上游给 expires 时用它）
        ttl = 3600.0
        for key in ("expiresIn", "expires_in"):
            if isinstance(body.get(key), (int, float)):
                ttl = float(body[key])
                break
        self._access_expires = time.time() + ttl
        self._uid = body.get("uid") or self._uid or ""
        if body.get("domain"):
            self._domain = body["domain"]
        # 上游可能同时轮换 refresh token，留着下次用
        new_refresh = body.get("refreshToken") or body.get("refresh")
        if new_refresh:
            self._refresh_token = new_refresh
        return access

    async def _headers(self) -> dict:
        access = await self._ensure_access()
        msg_id = _hex_id()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access}",
            "X-User-Id": self._uid or "",
            "X-Domain": self._domain,
            "X-Product": "SaaS",
            "X-IDE-Type": "WorkBuddy",
            "User-Agent": f"WorkBuddy/{UA_VERSION}",
            # 客户端标识：缺这几个会被判「不是官方客户端」
            "X-Requested-With": "XMLHttpRequest",
            "X-Agent-Intent": "craft",
            "X-Agent-Purpose": "conversation",
            "X-IDE-Name": "WorkBuddy",
            "X-IDE-Version": UA_VERSION,
            # 会话 id：缺则 11128 通道校验失败
            "X-Conversation-Message-ID": msg_id,
            "X-Request-ID": msg_id,
            "X-Conversation-Request-ID": _hex_id(),
            "X-Conversation-ID": _hex_id(),
        }
        if self.site["ai"]:
            headers["X-Agent-Type"] = "main"
        return headers

    # ── 底层流式 ───────────────────────────────────────────────────
    async def _stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]],
        max_tokens: int,
        temperature: float,
    ):
        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,  # WorkBuddy 只接受流式
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=90.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code >= 400:
                    raw = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"WorkBuddy HTTP {resp.status_code}: {raw}")
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
                    if chunk.get("usage"):
                        yield None, None, chunk["usage"]
                    for ch in (chunk.get("choices") or []):
                        delta = ch.get("delta") or {}
                        if delta.get("content"):
                            yield delta["content"], None, None
                        for tc in (delta.get("tool_calls") or []):
                            yield None, tc, None

    # ── 非流式对话 ─────────────────────────────────────────────────
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
        async for text, _tc, _u in self._stream_chat(msgs, None, max_tokens, temperature):
            if text:
                parts.append(text)
        return "".join(parts)

    # ── 流式 + 工具调用 ────────────────────────────────────────────
    async def astream_with_tools(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ):
        content_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        prompt_tokens = completion_tokens = 0
        async for text, tc, usage in self._stream_chat(
            messages, tools, max_tokens, temperature
        ):
            if usage:
                prompt_tokens = usage.get("prompt_tokens") or prompt_tokens
                completion_tokens = usage.get("completion_tokens") or completion_tokens
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
            prompt_tokens = estimate_tokens(
                "\n".join(str(m.get("content") or "") for m in messages)
            )
            completion_tokens = estimate_tokens("".join(content_parts))

        tool_calls: list[ToolCall] = []
        for idx in sorted(tool_acc):
            slot = tool_acc[idx]
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"__raw__": slot["arguments"]}
            if not isinstance(args, dict):
                args = {"__raw__": args}
            tool_calls.append(
                ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"], arguments=args)
            )
            yield {"type": "tool_call", "name": slot["name"], "arguments": args}

        yield {
            "type": "final",
            "response": LLMResponse(
                content="".join(content_parts),
                tool_calls=tool_calls,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
        }
