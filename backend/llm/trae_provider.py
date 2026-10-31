"""Trae Provider（CN / Global）—— 原生接入，不依赖 magpie（2026-10-31）

背景：Trae 的模型不在标准 OpenAI 端点上。协议由 magpie 插件
@magpie-community/opencode-trae-auth 逆向得出，本项目按其实现重写为 Python，
**凭证取自 magpie 已登录的账号**（`~/.config/magpie/plugin-auth.json` 里的
trae-cn / trae-global 条目），运行时不再需要 magpie。

实测协议（2026-10-31）:
  1. 换 token: POST {auth}/cloudide/api/v3/trae/oauth/ExchangeToken
     body {"ClientID","RefreshToken","ClientSecret":"-","UserID":""}
     → Result.{Token, RefreshToken, TokenExpireAt, UserInfo}
     **refresh token 每次都会轮换**，旧的花掉即失效。
  2. 对话: POST {api}/api/agent/v3/llm_utils_chat
     注意不是 /v1/chat/completions（那是 magpie 给 SDK 看的形状）。
  3. 响应是 SSE，事件名区分类型：
       metadata / timing_cost / output / extra_info / token_usage / done / error
     output 事件形如 {"response":"增量","reasoning_content":"...","tool_calls":[...]}

消息格式（实测 400 报错反推）: content 必须是**数组**
  {"role":"user","content":[{"type":"text","text":"..."}]}
助手带工具调用: {"role":"assistant","content":[...]|None,"tool_calls":[...]}
工具结果:      {"role":"tool","tool_call_id":"...","content":[{...}]}

凭证格式（填在系统设置的 API Key 处，整段粘贴 magpie 的凭证 JSON）:
  {"refresh":"...","uid":"...","machineId":"...","deviceId":"...","clientId":"..."}
"""

import json
import logging
import time
import uuid
from typing import Optional

import httpx

from backend.llm.base import BaseLLMProvider, LLMResponse, ToolCall, estimate_tokens

logger = logging.getLogger(__name__)

# 两个 realm 的站点表（实测自 magpie 插件）
SITES = {
    "trae-cn": {
        "name": "Trae CN",
        "auth": "https://api.trae.cn",
        "api": "https://trae-api-cn.mchost.guru",
        "app_id": "6eefa01c-1036-4c7e-9ca5-d891f63bfcd8",
        "client_version": "0.1.69",
        "client_version_code": "20260917",
        "default_model": "deepseek-v4.1-flash",
        # 客户端把 deepseek-v4.1-flash 只发给 solo_agent 这个 chat function
        "functions": ["chat_v3", "solo_work_lite", "solo_agent", "solo_agent_lite", "solo_coder"],
        "default_fn": "solo_agent",
    },
    "trae-global": {
        "name": "Trae Global",
        "auth": "https://growsg-normal.trae.ai",
        "api": "https://coresg-normal.trae.ai",
        "app_id": "6eefa01c-1036-4c7e-9ca5-d891f63bfcd8",
        "client_version": "0.1.69",
        "client_version_code": "20260917",
        "default_model": "deepseek-v4.1-flash",
        "functions": ["chat_v3", "solo_work_lite", "solo_agent", "solo_agent_lite", "solo_coder"],
        "default_fn": "solo_agent",
    },
}

# 客户端标识（IDE 自己的），缺了会被判非官方客户端
_BRAND = "ASUS TUF Gaming A15 FA507RM_FA507RM"


def parse_credential(raw: str) -> dict:
    """把 API Key 位置的凭证解析成 dict

    支持两种填法：
      1. 整段 JSON（magpie plugin-auth.json 里该 realm 的对象）—— 推荐；
      2. 纯 refresh token 字符串（其余字段缺省时不可用，会明确报错）。
    """
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
    return {"refresh": text}


class TraeProvider(BaseLLMProvider):
    """Trae CN / Global 的原生 Provider"""

    def __init__(
        self,
        model_name: str = "deepseek-v4.1-flash",
        api_key: str = "",
        base_url: str = "",
        realm: str = "trae-cn",
    ) -> None:
        site = SITES.get(realm) or SITES["trae-cn"]
        super().__init__(model_name, api_key, base_url or site["api"])
        self.site = site
        self.realm = realm if realm in SITES else "trae-cn"
        self._cred = parse_credential(api_key)
        self._token = ""
        self._token_exp = 0.0
        # 每个 provider 实例一条会话，避免每次请求都开新会话
        self._session_id = str(uuid.uuid4())

    # ── 鉴权 ───────────────────────────────────────────────────────
    def _missing(self) -> Optional[str]:
        need = ("refresh", "uid", "deviceId", "machineId")
        lack = [k for k in need if not self._cred.get(k)]
        if lack:
            return (
                "Trae 凭证不完整，缺少 %s。请在系统设置把 magpie 的凭证 JSON "
                "整段粘到 API Key（含 refresh/uid/deviceId/machineId）" % "/".join(lack)
            )
        return None

    async def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        err = self._missing()
        if err:
            raise RuntimeError(err)
        body = {
            "ClientID": self._cred.get("clientId") or "",
            "RefreshToken": self._cred.get("refresh") or "",
            "ClientSecret": "-",
            "UserID": "",
        }
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                f"{self.site['auth']}/cloudide/api/v3/trae/oauth/ExchangeToken", json=body
            )
        if r.status_code >= 400:
            raise RuntimeError(
                f"{self.site['name']} 换 token 失败: HTTP {r.status_code} {r.text[:160]}"
            )
        data = r.json()
        res = data.get("Result") or data.get("result") or {}
        token = res.get("Token") or res.get("token") or ""
        if not token:
            raise RuntimeError(f"{self.site['name']} 未返回 token: {str(data)[:160]}")
        self._token = token
        # Trae 每次换 token 都会轮换 refresh，旧的花掉即失效。
        # 这里只在内存里更新；如需长期可用，请把新的 refresh 写回系统设置
        # （轮换后旧值失效，重启会认证失败）。
        new_refresh = res.get("RefreshToken") or res.get("refreshToken")
        if new_refresh and new_refresh != self._cred.get("refresh"):
            self._cred["refresh"] = new_refresh
            # 回写 system_config，让轮换后的凭证跨重启存活（2026-10-31）。
            # 旧值在服务端已作废，不回写的话下次启动必然认证失败。
            from backend.llm.credential_store import persist_credential
            await persist_credential(self.realm, {"refresh": new_refresh})
        exp = res.get("TokenExpireAt") or res.get("tokenExpireAt")
        if isinstance(exp, (int, float)) and exp > 1e12:
            self._token_exp = exp / 1000.0
        elif isinstance(exp, (int, float)) and exp > 0:
            self._token_exp = float(exp)
        else:
            self._token_exp = time.time() + 1800
        return token

    def _headers(self, token: str, stream: bool) -> dict:
        s = self.site
        c = self._cred
        return {
            "Content-Type": "application/json",
            "Authorization": f"Cloud-IDE-JWT {token}",
            "X-Cloudide-Token": token,
            "x-ide-token": token,
            "x-uid": str(c.get("uid") or ""),
            "x-app-id": s["app_id"],
            "x-device-id": str(c.get("deviceId") or ""),
            "x-machine-id": str(c.get("machineId") or ""),
            "x-request-id": str(uuid.uuid4()),
            "x-app-version": s["client_version"],
            "x-app-version-code": s["client_version_code"],
            "x-ide-version": s["client_version"],
            "x-ide-version-code": s["client_version_code"],
            "x-ide-version-type": "stable",
            "x-device-cpu": "AMD",
            "x-device-brand": _BRAND,
            "x-device-type": "windows",
            "x-os-version": "Windows 10",
            "x-system-type": "Windows",
            "Accept": "text/event-stream" if stream else "application/json",
        }

    # ── 消息与请求体 ───────────────────────────────────────────────
    @staticmethod
    def _content(text: str) -> list:
        return [{"type": "text", "text": text or ""}]

    def _messages(self, messages: list[dict]) -> list[dict]:
        """OpenAI messages → Trae messages（content 必须是数组）"""
        out: list[dict] = []
        for m in messages or []:
            role = m.get("role") or "user"
            content = m.get("content")
            text = content if isinstance(content, str) else (
                "".join(p.get("text", "") for p in content if isinstance(p, dict))
                if isinstance(content, list) else ""
            )
            if role in ("system", "developer"):
                out.append({"role": "system", "content": self._content(text)})
            elif role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id") or "",
                    "content": self._content(text),
                })
            elif role == "assistant" and m.get("tool_calls"):
                # 历史里的工具调用必须用 function_call（与响应同形状），
                # 且 arguments 是**字符串**；写成 OpenAI 的 function 会让
                # 第 2 轮直接 2001「ChatStream failed」（实测踩过）。
                calls = []
                for tc in m["tool_calls"]:
                    fn = tc.get("function") or {}
                    name = str(fn.get("name") or "").strip()
                    if not name:
                        continue
                    args = fn.get("arguments")
                    if not isinstance(args, str):
                        try:
                            args = json.dumps(args or {}, ensure_ascii=False)
                        except (TypeError, ValueError):
                            args = "{}"
                    calls.append({
                        "id": str(tc.get("id") or ""),
                        "type": "function",
                        "function_call": {"name": name, "arguments": args},
                    })
                if calls:
                    out.append({
                        "role": "assistant",
                        "content": self._content(text) if text else None,
                        "tool_calls": calls,
                    })
                elif text:
                    out.append({"role": "assistant", "content": self._content(text)})
            else:
                out.append({"role": role, "content": self._content(text)})
        return out

    def _body(
        self, messages: list[dict], tools: Optional[list[dict]],
        max_tokens: int, temperature: float,
    ) -> dict:
        model = self.model_name
        # 去掉 -max 后缀：-max 是 magpie 给「同模型更高档」起的别名，
        # Trae 的 config_name 要基础名
        base = model[:-4] if model.endswith("-max") else model
        body = {
            "messages": self._messages(messages),
            "function": self.site["default_fn"],
            "config_name": base,
            "model": base,
            "stream": True,  # Trae 只回 SSE
            "request_id": str(uuid.uuid4()),
            "session_id": self._session_id,
        }
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        if isinstance(temperature, (int, float)):
            body["temperature"] = temperature
        if tools:
            # Trae 要求 function.parameters 是 **JSON 字符串**而非对象
            # （实测 4001: cannot unmarshal object into ... parameters of type string）
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": (t.get("function") or {}).get("name", ""),
                        "description": (t.get("function") or {}).get("description", ""),
                        "parameters": json.dumps(
                            (t.get("function") or {}).get("parameters") or {},
                            ensure_ascii=False,
                        ),
                    },
                }
                for t in tools
                if (t.get("function") or {}).get("name")
            ]
            body["tool_choice"] = "auto"
        return body

    # ── 流式 ───────────────────────────────────────────────────────
    async def _stream(
        self, messages: list[dict], tools: Optional[list[dict]],
        max_tokens: int, temperature: float,
    ):
        """yield (text, reasoning, tool_call_dict, usage)"""
        token = await self._ensure_token()
        body = self._body(messages, tools, max_tokens, temperature)
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.site['api']}/api/agent/v3/llm_utils_chat",
                json=body,
                headers=self._headers(token, stream=True),
            ) as resp:
                if resp.status_code >= 400:
                    raw = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"{self.site['name']} HTTP {resp.status_code}: {raw}")
                event = ""
                async for line in resp.aiter_lines():
                    if not line:
                        event = ""
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if event == "error":
                        raise RuntimeError(
                            f"{self.site['name']} {data.get('code')}: {data.get('message')}"
                        )
                    if event == "token_usage":
                        yield None, None, None, {
                            "prompt_tokens": data.get("prompt_tokens") or 0,
                            "completion_tokens": data.get("completion_tokens") or 0,
                        }
                    elif event == "output":
                        if data.get("reasoning_content"):
                            yield None, data["reasoning_content"], None, None
                        if data.get("response"):
                            yield data["response"], None, None, None
                        for tc in (data.get("tool_calls") or []):
                            yield None, None, tc, None
                    elif event == "done":
                        return

    # ── 非流式对话 ─────────────────────────────────────────────────
    async def chat(
        self, system_prompt: str, messages: list[dict],
        max_tokens: int = 2048, temperature: float = 0.7,
    ) -> str:
        msgs = [{"role": "system", "content": system_prompt}] if system_prompt else []
        msgs.extend(messages)
        parts: list[str] = []
        async for text, _r, _tc, _u in self._stream(msgs, None, max_tokens, temperature):
            if text:
                parts.append(text)
        return "".join(parts)

    # ── 流式 + 工具调用 ────────────────────────────────────────────
    async def astream_with_tools(
        self, messages: list[dict], tools: Optional[list[dict]] = None,
        max_tokens: int = 4096, temperature: float = 0.3,
    ):
        content: list[str] = []
        reasoning: list[str] = []
        tool_acc: dict[int, dict] = {}
        prompt_tokens = completion_tokens = 0
        async for text, think, tc, usage in self._stream(
            messages, tools, max_tokens, temperature
        ):
            if usage:
                prompt_tokens = usage.get("prompt_tokens") or prompt_tokens
                completion_tokens = usage.get("completion_tokens") or completion_tokens
            if think:
                reasoning.append(think)
            if text:
                content.append(text)
                yield {"type": "delta", "text": text}
            if tc:
                idx = tc.get("index")
                if not isinstance(idx, int):
                    idx = len(tool_acc)
                slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                # Trae 的字段名是 function_call（不是 OpenAI 的 function），
                # 且 arguments 以**字符串分片**流式下发：
                #   第 1 片带 id+name 与参数开头，后续片只带参数增量
                fn = tc.get("function_call") or tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                args = fn.get("arguments")
                if isinstance(args, str) and args:
                    slot["arguments"] += args
                elif isinstance(args, dict):
                    slot["arguments"] += json.dumps(args, ensure_ascii=False)

        if not (prompt_tokens or completion_tokens):
            prompt_tokens = estimate_tokens(
                "\n".join(str(m.get("content") or "") for m in messages)
            )
            completion_tokens = estimate_tokens("".join(content))

        calls: list[ToolCall] = []
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
            calls.append(ToolCall(id=slot["id"] or f"call_{idx}",
                                  name=slot["name"], arguments=args))
            yield {"type": "tool_call", "name": slot["name"], "arguments": args}

        yield {
            "type": "final",
            "response": LLMResponse(
                content="".join(content), tool_calls=calls,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                reasoning="".join(reasoning),
            ),
        }
