"""Qoder Provider（qoder / qoder-cn）—— 原生接入，不依赖 magpie（2026-10-31）

背景：Qoder 的模型接口既不是 OpenAI 也不是 Anthropic，而是自家协议：
  * 请求要带 **COSY 信封**：AES-128-CBC 加密用户信息，AES 密钥再用 Qoder
    内置的 1024 位 RSA 公钥包裹；再对 payload/key/时间/body/path 做 MD5 签名。
  * body 要经**自定义编码**（打乱字母表的 base64 变体 + 前后三分之一互换）。
  * 响应是 SSE，但每行是 {"body": "<OpenAI chunk 的 JSON 字符串>"} 的信封。

协议来自 magpie 插件 @magpie-community/opencode-qoder-auth 的逆向实现，
本项目按其重写为 Python；**凭证取自 magpie 已登录的账号**，运行时不依赖 magpie。

实测（2026-10-31，qoder-cn）：
  - 模型清单 GET /algo/api/v2/model/list?Encode=1 → 明文 JSON
  - 对话 POST /algo/api/v2/service/pro/sse/agent_chat_generation → SSE
  - `dfmodel`（DeepSeek-Flash）price_factor=0.0，即免费
  - 缺 Cosy-User / Login-Version 会报 403 "Signature invalid"（易误判为签名算错）

凭证格式（填在系统设置的 API Key 处，整段粘贴 magpie 的凭证 JSON）:
  {"access":"...","refresh":"...","uid":"...","machineId":"..."}
  qoder-cn 走 deviceChat：access 就是设备令牌，直接用于 COSY。
"""

import base64
import hashlib
import json
import logging
import time
import urllib.parse
import uuid
from typing import Optional

import httpx

from backend.llm.base import BaseLLMProvider, LLMResponse, ToolCall, estimate_tokens

logger = logging.getLogger(__name__)


class _AuthExpired(Exception):
    """令牌过期/被拒：由 _stream 捕获后刷新重试一次"""


SITES = {
    "qoder": {
        "name": "Qoder",
        "api": "https://gateway.qoder.com.cn",
        "openapi": "https://openapi.qoder.com.cn",
        "cosy_version": "1.1.69",
        "default_model": "dfmodel",
    },
    "qoder-cn": {
        "name": "Qoder CN",
        "api": "https://gateway.qoder.com.cn",
        "openapi": "https://openapi.qoder.com.cn",
        "cosy_version": "1.1.69",
        "default_model": "dfmodel",
    },
}

MODELS_PATH = "/algo/api/v2/model/list?Encode=1"
CHAT_PATH = (
    "/algo/api/v2/service/pro/sse/agent_chat_generation"
    "?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1"
)

# Qoder 客户端内置的公钥，用来包裹每次请求的 AES 密钥
RSA_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA8iMH5c02LilrsERw9t6Pv5Nc
4k6Pz1EaDicBMpdpxKduSZu5OANqUq8er4GM95omAGIOPOh+Nx0spthYA2BqGz+l
6HRkPJ7S236FZz73In/KVuLnwI8JJ2CbuJap8kvheCCZpmAWpb/cPx/3Vr/J6I17
XcW+ML9FoCI6AOvOzwIDAQAB
-----END PUBLIC KEY-----"""

# body 编码用的打乱字母表（'$' 作填充）
ALPHABET = "_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!"


def _compact(obj) -> str:
    """与 JS JSON.stringify 一致的紧凑序列化（不能有空格，否则签名不符）"""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _pkcs7(data: bytes) -> bytes:
    n = 16 - len(data) % 16
    return data + bytes([n]) * n


def _segment_encode(data: bytes) -> str:
    out = []
    acc = 0
    nbits = 0
    for b in data:
        acc = ((acc << 8) | b) & 0xFFFFFF
        nbits += 8
        while nbits >= 6:
            nbits -= 6
            out.append(ALPHABET[(acc >> nbits) & 63])
    if nbits > 0:
        out.append(ALPHABET[(acc << (6 - nbits)) & 63])
    s = "".join(out)
    while len(s) % 4:
        s += "$"
    return s


def _swap_thirds(s: str) -> str:
    t = len(s) // 3
    if not t:
        return s
    return s[len(s) - t:] + s[t:len(s) - t] + s[:t]


def encode_body(text: str) -> str:
    """请求体编码：打乱字母表 base64 → 前后三分之一互换"""
    return _swap_thirds(_segment_encode(text.encode("utf-8")))


def parse_credential(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
    return {}


class QoderProvider(BaseLLMProvider):
    """Qoder / Qoder CN 的原生 Provider"""

    def __init__(
        self,
        model_name: str = "dfmodel",
        api_key: str = "",
        base_url: str = "",
        realm: str = "qoder-cn",
    ) -> None:
        site = SITES.get(realm) or SITES["qoder-cn"]
        super().__init__(model_name, api_key, base_url or site["api"])
        self.site = site
        self.realm = realm if realm in SITES else "qoder-cn"
        self._cred = parse_credential(api_key)
        self._model_entry: Optional[dict] = None

    # ── COSY 信封 ──────────────────────────────────────────────────
    def _user_blob(self) -> dict:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        c = self._cred
        raw = _compact({
            "uid": c.get("uid") or "",
            "aid": "",
            "name": c.get("name") or "",
            "email": c.get("email") or "",
            "security_oauth_token": c.get("access") or "",
        })
        key = uuid.uuid4().hex[:16].encode()
        enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
        info = base64.b64encode(
            enc.update(_pkcs7(raw.encode("utf-8"))) + enc.finalize()
        ).decode()
        pub = serialization.load_pem_public_key(RSA_KEY.encode())
        wrapped = base64.b64encode(pub.encrypt(key, padding.PKCS1v15())).decode()
        return {"info": info, "key": wrapped}

    def _cosy_headers(
        self, url: str, body: str = "", chat: bool = False,
        model_key: str = "", model_source: str = "",
    ) -> dict:
        c = self._cred
        ts = int(time.time())
        blob = self._user_blob()
        payload = base64.b64encode(_compact({
            "version": "v1",
            "requestId": str(uuid.uuid4()),
            "info": blob["info"],
            "cosyVersion": self.site["cosy_version"],
            "ideVersion": "",
        }).encode()).decode()
        path = urllib.parse.urlparse(url).path
        if path.startswith("/algo"):
            path = path[5:]
        sig = hashlib.md5(
            f"{payload}\n{blob['key']}\n{ts}\n{body}\n{path}".encode()
        ).hexdigest()
        m = c.get("machineId") or ""
        h = {
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "Authorization": f"Bearer COSY.{payload}.{sig}",
            "Cosy-Business-Product": "app",
            "Cosy-Business-Type": "agent",
            "Cosy-Data-Policy": "disagree",
            "Cosy-Date": str(ts),
            "Cosy-Key": blob["key"],
            "Cosy-MachineId": m,
            "Cosy-MachineOS": "x86_64_win32",
            "Cosy-Scene": "app",
            # 这两个头漏了会 403 "Signature invalid"（实测踩过，极易误判）
            "Cosy-User": c.get("uid") or "",
            "Cosy-Version": self.site["cosy_version"],
            "Login-Version": "v2",
        }
        if chat:
            h.update({
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Model-Key": model_key,
                "X-Model-Source": model_source,
                "Cosy-MachineHostname": "fund-assistant",
            })
        else:
            h.update({
                "Accept": "application/json",
                "Cosy-ClientIp": m,
                "Cosy-MachineToken": m,
                "Cosy-MachineType": "5",
            })
        return h

    async def _refresh_access(self) -> bool:
        """设备令牌刷新（qoder-cn 走 deviceChat）

        实测: POST {openapi}/api/v1/deviceToken/refresh {"refresh_token": ...}
              → {device_token, refresh_token, expires_at}
        **refresh_token 会轮换**，所以必须回写，否则下次启动用旧值必失败。
        """
        rt = (self._cred.get("refresh") or "").strip()
        if not rt:
            return False
        url = self.site["openapi"] + "/api/v1/deviceToken/refresh"
        try:
            async with httpx.AsyncClient(timeout=25.0) as c:
                r = await c.post(url, json={"refresh_token": rt})
            if r.status_code >= 400:
                logger.warning(
                    f"{self.site['name']} 刷新设备令牌失败: HTTP {r.status_code} {r.text[:120]}"
                )
                return False
            data = r.json()
        except Exception as e:  # noqa: BLE001 - 刷新失败不该抛出，由调用方报错
            logger.warning(f"{self.site['name']} 刷新设备令牌异常: {type(e).__name__}: {e}")
            return False

        token = data.get("device_token") or data.get("token") or ""
        if not token:
            logger.warning(f"{self.site['name']} 刷新未返回 device_token")
            return False
        self._cred["access"] = token
        new_rt = data.get("refresh_token") or data.get("refreshToken")
        changes = {"access": token}
        if new_rt and new_rt != rt:
            self._cred["refresh"] = new_rt
            changes["refresh"] = new_rt
        # 回写让轮换后的凭证跨重启存活
        from backend.llm.credential_store import persist_credential
        await persist_credential(self.realm, changes)
        logger.info(f"{self.site['name']} 设备令牌已刷新")
        return True

    def _missing(self) -> Optional[str]:
        lack = [k for k in ("access", "uid", "machineId") if not self._cred.get(k)]
        if lack:
            return (
                "Qoder 凭证不完整，缺少 %s。请在系统设置把 magpie 的凭证 JSON "
                "整段粘到 API Key（含 access/uid/machineId）" % "/".join(lack)
            )
        return None

    async def _ensure_model_entry(self) -> dict:
        """取模型清单里的条目（chat 请求要带 model_config）"""
        if self._model_entry is not None:
            return self._model_entry
        err = self._missing()
        if err:
            raise RuntimeError(err)
        url = self.site["api"] + MODELS_PATH
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(url, headers=self._cosy_headers(url))
        if r.status_code >= 400:
            raise RuntimeError(
                f"{self.site['name']} 模型清单失败: HTTP {r.status_code} {r.text[:160]}"
            )
        data = r.json()
        wanted = self.model_name
        pool = []
        for scene in ("chat", "app"):
            pool.extend(data.get(scene) or [])
        entry = None
        for m in pool:
            if str(m.get("key")) == wanted:
                entry = m
                break
        if entry is None:
            keys = sorted({str(m.get("key")) for m in pool if m.get("key")})
            raise RuntimeError(
                f"{self.site['name']} 没有模型 {wanted}；可用: {', '.join(keys[:12])}"
            )
        self._model_entry = entry
        return entry

    # ── 请求体 ─────────────────────────────────────────────────────
    def _chat_body(self, messages: list[dict], tools: Optional[list[dict]],
                   max_tokens: int, temperature: float, entry: dict) -> dict:
        system_text = "\n\n".join(
            (m.get("content") if isinstance(m.get("content"), str) else "")
            for m in messages if (m.get("role") in ("system", "developer"))
        )
        sys_obj = {"type": "text", "text": system_text or "You are a Qoder agent."}

        conv: list[dict] = []
        for m in messages or []:
            role = m.get("role") or "user"
            if role in ("system", "developer"):
                continue
            content = m.get("content")
            text = content if isinstance(content, str) else (
                "".join(p.get("text", "") for p in content if isinstance(p, dict))
                if isinstance(content, list) else ""
            )
            if role == "tool":
                conv.append({
                    "role": "user",
                    "content": [{"type": "text", "text": f"工具结果：{text}"}],
                })
            elif role == "assistant" and m.get("tool_calls"):
                calls = "；".join(
                    f"{((tc.get('function') or {}).get('name'))}({((tc.get('function') or {}).get('arguments'))})"
                    for tc in m["tool_calls"]
                )
                conv.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"{text}\n[调用工具] {calls}".strip()}],
                })
            else:
                conv.append({"role": role, "content": [{"type": "text", "text": text}]})

        rid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        body = {
            "request_id": rid,
            "request_set_id": tid,
            "chat_record_id": rid,
            "session_id": str(uuid.uuid4()),
            "parameters": {
                "enable_thinking": False,
                "max_tokens": int(max_tokens or 4096),
            },
            "business": {
                "product": "app", "version": self.site["cosy_version"],
                "type": "agent", "id": tid, "name": "",
                "begin_at": int(time.time() * 1000), "stage": "start",
            },
            "agent_id": "agent_common",
            "task_id": "common",
            "session_type": "app",
            "model_config": entry,
            "system": [sys_obj],
            "messages": [{"role": "system", "content": [sys_obj]}, *conv],
        }
        if isinstance(temperature, (int, float)):
            body["parameters"]["temperature"] = temperature
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": (t.get("function") or {}).get("name", ""),
                        "description": (t.get("function") or {}).get("description", ""),
                        "parameters": (t.get("function") or {}).get("parameters") or {},
                    },
                }
                for t in tools
                if (t.get("function") or {}).get("name")
            ]
        return body

    # ── 流式 ───────────────────────────────────────────────────────
    async def _stream(self, messages: list[dict], tools: Optional[list[dict]],
                      max_tokens: int, temperature: float):
        """带一次令牌刷新的流式（401/403 → 刷新 → 重试一次）

        令牌过期是常态（设备令牌有有效期），刷新后原样重放；
        只重试一次，避免凭证真的失效时反复打转。
        """
        try:
            async for item in self._stream_once(messages, tools, max_tokens, temperature):
                yield item
            return
        except _AuthExpired:
            pass
        if not await self._refresh_access():
            raise RuntimeError(f"{self.site['name']} 令牌已过期且刷新失败，请重新配置凭证")
        async for item in self._stream_once(messages, tools, max_tokens, temperature):
            yield item

    async def _stream_once(self, messages: list[dict], tools: Optional[list[dict]],
                           max_tokens: int, temperature: float):
        """单次尝试，yield (text, reasoning, tool_call, usage)"""
        entry = await self._ensure_model_entry()
        body = self._chat_body(messages, tools, max_tokens, temperature, entry)
        wire = encode_body(_compact(body))
        url = self.site["api"] + CHAT_PATH
        headers = self._cosy_headers(
            url, wire, chat=True,
            model_key=str(entry.get("key") or ""),
            model_source=str(entry.get("source") or ""),
        )
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream("POST", url, content=wire.encode(), headers=headers) as resp:
                if resp.status_code in (401, 403):
                    # 交由 _stream 统一处理：刷新令牌后重放一次
                    raise _AuthExpired()
                if resp.status_code >= 400:
                    raw = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"{self.site['name']} HTTP {resp.status_code}: {raw}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        env = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    code = env.get("statusCodeValue")
                    inner = env.get("body") if isinstance(env.get("body"), str) else ""
                    if code is not None and code != 200:
                        raise RuntimeError(f"{self.site['name']} {code}: {inner[:200]}")
                    if inner.strip() == "[DONE]":
                        return
                    if not inner:
                        continue
                    try:
                        chunk = json.loads(inner)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        u = chunk["usage"]
                        yield None, None, None, {
                            "prompt_tokens": u.get("prompt_tokens") or 0,
                            "completion_tokens": u.get("completion_tokens") or 0,
                        }
                    for ch in (chunk.get("choices") or []):
                        delta = ch.get("delta") or {}
                        if delta.get("reasoning_content"):
                            yield None, delta["reasoning_content"], None, None
                        if isinstance(delta.get("content"), str) and delta["content"]:
                            yield delta["content"], None, None, None
                        for tc in (delta.get("tool_calls") or []):
                            yield None, None, tc, None

    # ── 非流式 ─────────────────────────────────────────────────────
    async def chat(self, system_prompt: str, messages: list[dict],
                   max_tokens: int = 2048, temperature: float = 0.7) -> str:
        msgs = [{"role": "system", "content": system_prompt}] if system_prompt else []
        msgs.extend(messages)
        parts: list[str] = []
        async for text, _r, _tc, _u in self._stream(msgs, None, max_tokens, temperature):
            if text:
                parts.append(text)
        return "".join(parts)

    # ── 流式 + 工具 ────────────────────────────────────────────────
    async def astream_with_tools(self, messages: list[dict],
                                 tools: Optional[list[dict]] = None,
                                 max_tokens: int = 4096, temperature: float = 0.3):
        content: list[str] = []
        reasoning: list[str] = []
        tool_acc: dict[int, dict] = {}
        prompt_tokens = completion_tokens = 0
        async for text, think, tc, usage in self._stream(messages, tools, max_tokens, temperature):
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
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if isinstance(fn.get("arguments"), str):
                    slot["arguments"] += fn["arguments"]

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
