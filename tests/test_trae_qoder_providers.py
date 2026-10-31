"""Trae / Qoder 原生 Provider（2026-10-31）

两者协议都不是标准 OpenAI 接口，凭证取自 magpie 已登录账号：
  - Trae：ExchangeToken 换 JWT → POST /api/agent/v3/llm_utils_chat（自定义 body）
  - Qoder：COSY 信封（AES+RSA+MD5 签名）+ 自定义 body 编码 → SSE 信封

本文件只测纯函数与请求构造，**不打网络**（CI 红线）。
真实链路已手工验证：Trae Global 对话+工具调用通过；Qoder CN 对话+工具调用通过。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.llm.qoder_provider import (  # noqa: E402
    ALPHABET, QoderProvider, _compact, _pkcs7, encode_body,
)
from backend.llm.trae_provider import TraeProvider, parse_credential  # noqa: E402


def _qoder_decode(wire: str) -> str:
    """测试用的逆运算（生产不需要）"""
    t = len(wire) // 3
    s = wire[len(wire) - t:] + wire[t:len(wire) - t] + wire[:t] if t else wire
    out = bytearray()
    for g in range(0, len(s) - len(s) % 4, 4):
        vals = [ALPHABET.index(c) for c in s[g:g + 4] if c != "$"]
        x = 0
        for v in vals:
            x = x * 64 + v
        n = (6 * len(vals)) // 8
        for k in range(n):
            out.append((x >> (6 * len(vals) - 8 * (k + 1))) & 255)
    return out.decode("utf-8")


class TestQoderBodyCodec:
    def test_alphabet_is_64_unique(self):
        assert len(ALPHABET) == 64
        assert len(set(ALPHABET)) == 64

    @pytest.mark.parametrize("text", [
        '{"a":1}',
        '{"messages":[{"role":"user","content":[{"type":"text","text":"中文测试"}]}]}',
        "x" * 500,
        "",
    ])
    def test_roundtrip(self, text):
        assert _qoder_decode(encode_body(text)) == text, "编码必须可逆"

    def test_output_length_multiple_of_four(self):
        for n in (1, 2, 3, 7, 100):
            assert len(encode_body("a" * n)) % 4 == 0

    def test_compact_json_has_no_spaces(self):
        """与 JS JSON.stringify 一致：带空格会导致签名不匹配"""
        s = _compact({"a": 1, "b": "x"})
        assert s == '{"a":1,"b":"x"}'
        assert " " not in s

    def test_pkcs7_pads_to_block(self):
        assert len(_pkcs7(b"1234567890123456")) == 32, "整块时要补一整块"
        assert len(_pkcs7(b"abc")) == 16


class TestQoderRequestShape:
    def test_cosy_headers_include_critical_fields(self):
        """Cosy-User / Login-Version 漏了会 403 Signature invalid（实测踩过）"""
        p = QoderProvider(api_key=json.dumps({
            "access": "tok", "uid": "u1", "machineId": "m1",
        }))
        h = p._cosy_headers("https://gateway.qoder.com.cn/algo/api/v2/model/list?Encode=1")
        for k in ("Cosy-User", "Login-Version", "Cosy-Key", "Cosy-Date",
                  "Cosy-MachineId", "Cosy-Version", "Authorization"):
            assert k in h, f"{k} 必须存在"
        assert h["Cosy-User"] == "u1"
        assert h["Authorization"].startswith("Bearer COSY.")

    def test_chat_headers_use_stream_accept(self):
        p = QoderProvider(api_key=json.dumps({
            "access": "tok", "uid": "u1", "machineId": "m1",
        }))
        h = p._cosy_headers("https://x/algo/api/v2/service/pro/sse/agent_chat_generation?Encode=1",
                            body="abc", chat=True, model_key="dfmodel", model_source="system")
        assert h["Accept"] == "text/event-stream"
        assert h["X-Model-Key"] == "dfmodel"

    def test_signature_changes_with_body(self):
        """签名必须覆盖 body，否则服务端拒绝"""
        p = QoderProvider(api_key=json.dumps({
            "access": "tok", "uid": "u1", "machineId": "m1",
        }))
        url = "https://x/algo/api/v2/model/list?Encode=1"
        a = p._cosy_headers(url, body="")["Authorization"].split(".")[-1]
        b = p._cosy_headers(url, body="zzz")["Authorization"].split(".")[-1]
        assert a != b

    def test_missing_credential_is_clear(self):
        p = QoderProvider(api_key="")
        assert "凭证不完整" in (p._missing() or "")

    def test_chat_body_has_required_fields(self):
        p = QoderProvider(model_name="dfmodel")
        body = p._chat_body(
            [{"role": "system", "content": "sys"},
             {"role": "user", "content": "hi"}], None, 100, 0.3, {"key": "dfmodel"})
        for k in ("request_id", "request_set_id", "session_id", "parameters",
                  "business", "model_config", "system", "messages"):
            assert k in body, f"{k} 缺失"
        assert body["messages"][0]["role"] == "system"


class TestTraeRequestShape:
    def test_parse_credential_json(self):
        c = parse_credential('{"refresh":"r","uid":"u","deviceId":"d","machineId":"m"}')
        assert c["refresh"] == "r" and c["uid"] == "u"

    def test_parse_credential_bare_refresh(self):
        assert parse_credential("abc") == {"refresh": "abc"}
        assert parse_credential("") == {}

    def test_missing_credential_is_clear(self):
        p = TraeProvider(api_key="", realm="trae-cn")
        assert "凭证不完整" in (p._missing() or "")

    def test_messages_content_is_array(self):
        """Trae 要求 content 为数组（实测 400 反推）"""
        p = TraeProvider(api_key="{}", realm="trae-cn")
        out = p._messages([{"role": "user", "content": "hi"}])
        assert isinstance(out[0]["content"], list)
        assert out[0]["content"][0]["type"] == "text"

    def test_tool_result_role(self):
        p = TraeProvider(api_key="{}", realm="trae-cn")
        out = p._messages([{"role": "tool", "tool_call_id": "c1", "content": "r"}])
        assert out[0]["role"] == "tool" and out[0]["tool_call_id"] == "c1"

    def test_tools_parameters_must_be_string(self):
        """parameters 要 JSON 字符串（实测 4001: cannot unmarshal object）"""
        p = TraeProvider(api_key="{}", realm="trae-cn")
        body = p._body(
            [{"role": "user", "content": "x"}],
            [{"type": "function", "function": {
                "name": "f", "description": "d",
                "parameters": {"type": "object", "properties": {}}}}],
            100, 0.3)
        assert isinstance(body["tools"][0]["function"]["parameters"], str)
        assert json.loads(body["tools"][0]["function"]["parameters"])["type"] == "object"

    def test_body_uses_solo_agent_function(self):
        p = TraeProvider(api_key="{}", realm="trae-cn")
        body = p._body([{"role": "user", "content": "x"}], None, 100, 0.3)
        assert body["function"] == "solo_agent", "deepseek-v4.1-flash 只在 solo_agent 下提供"

    def test_max_suffix_stripped(self):
        """magpie 的 -max 别名要去掉，Trae 只认基础名"""
        p = TraeProvider(model_name="deepseek-v4.1-flash-max", api_key="{}")
        body = p._body([{"role": "user", "content": "x"}], None, 100, 0.3)
        assert body["config_name"] == "deepseek-v4.1-flash"


class TestFactoryWiring:
    @pytest.mark.parametrize("key,cls", [
        ("trae-cn", TraeProvider), ("trae-global", TraeProvider),
        ("qoder-cn", QoderProvider), ("qoder", QoderProvider),
    ])
    def test_factory_builds(self, key, cls):
        from backend.llm.factory import LLMFactory
        p = LLMFactory.create(key, "{}", "")
        assert isinstance(p, cls)

    def test_default_models(self):
        from backend.llm.factory import LLMFactory
        assert LLMFactory.create("trae-cn", "{}", "").model_name == "deepseek-v4.1-flash"
        assert LLMFactory.create("qoder-cn", "{}", "").model_name == "dfmodel"

    def test_presets_registered(self):
        from backend.routers.system_config import _AI_MODEL_PRESETS
        keys = {p.key for p in _AI_MODEL_PRESETS}
        for k in ("trae-cn", "trae-global", "qoder-cn"):
            assert k in keys, f"预设缺 {k}"

    def test_cryptography_declared(self):
        """Qoder 依赖 cryptography，必须在 requirements 里（否则镜像静默失败）"""
        from pathlib import Path
        req = (Path(__file__).resolve().parent.parent / "backend/requirements.txt").read_text()
        assert "cryptography" in req
