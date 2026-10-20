"""LLM 备用链路（magpie 兜底）——默认关闭，启用后主链路失败自动改写

对应 backend/llm/fallback.py 与 ai_service/agent_runner 的接入点。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.llm.fallback import (  # noqa: E402
    describe_fallback, load_fallback_config,
)


class TestLoadFallbackConfig:
    def test_disabled_by_default(self):
        """没配任何 ai_fallback_* → 返回 None，主链路行为不变"""
        assert load_fallback_config({}) is None
        assert load_fallback_config({"ai_api_key": "sk-main"}) is None

    def test_requires_explicit_switch(self):
        """只填了 Key 但没开关 → 仍然不启用（避免误开）"""
        cfg = {
            "ai_fallback_api_key": "sk-fb",
            "ai_fallback_model_id": "opencode-zen-free/deepseek-v4.1-flash-free",
        }
        assert load_fallback_config(cfg) is None

    def test_enabled_with_switch_and_key(self):
        cfg = {
            "ai_fallback_enabled": "true",
            "ai_fallback_api_key": "sk-fb",
            "ai_fallback_base_url": "http://127.0.0.1:3425/v1",
            "ai_fallback_model_id": "opencode-zen-free/deepseek-v4.1-flash-free",
        }
        got = load_fallback_config(cfg)
        assert got is not None
        assert got["api_key"] == "sk-fb"
        assert got["base_url"] == "http://127.0.0.1:3425/v1"
        assert got["model_id"] == "opencode-zen-free/deepseek-v4.1-flash-free"
        assert got["model"] == "openai", "缺省预设应为 openai 兼容"

    def test_switch_on_but_no_key_is_none(self):
        """开关打开但 Key 为空 → 不启用，避免每轮白试一次"""
        assert load_fallback_config({
            "ai_fallback_enabled": "true",
            "ai_fallback_api_key": "   ",
        }) is None

    def test_base_url_falls_back_to_main(self):
        cfg = {
            "ai_fallback_enabled": "1",
            "ai_fallback_api_key": "sk-fb",
            "ai_base_url": "https://api.deepseek.com/v1",
        }
        got = load_fallback_config(cfg)
        assert got is not None
        assert got["base_url"] == "https://api.deepseek.com/v1"

    def test_describe_never_leaks_key(self):
        cfg = {
            "ai_fallback_enabled": "true",
            "ai_fallback_api_key": "sk-super-secret",
            "ai_fallback_base_url": "http://127.0.0.1:3425/v1",
        }
        text = describe_fallback(load_fallback_config(cfg))
        assert "sk-super-secret" not in text
        assert "127.0.0.1:3425" in text
        assert describe_fallback(None) == "未启用"


class TestAiServiceFallback:
    """ai_service 走备用链路的条件与结果"""

    @staticmethod
    def _cm(**kw):
        base = {
            "ai_model": "deepseek",
            "ai_api_key": "sk-main",
            "ai_base_url": "https://api.deepseek.com/v1",
        }
        base.update(kw)
        return base

    def test_fallback_config_ignored_when_off(self):
        cm = self._cm(ai_fallback_api_key="sk-fb")  # 有 Key 但没开关
        assert load_fallback_config(cm) is None

    def test_fallback_config_used_when_on(self):
        cm = self._cm(
            ai_fallback_enabled="true",
            ai_fallback_api_key="sk-fb",
            ai_fallback_base_url="http://192.168.1.25:3425/v1",
            ai_fallback_model_id="opencode-zen-free/deepseek-v4.1-flash-free",
        )
        got = load_fallback_config(cm)
        assert got and got["api_key"] == "sk-fb"


class TestAgentErrorHint:
    """错误提示构造（两条失败路径共用，回归护栏）"""

    def test_tool_hint_preserved(self):
        from backend.ai.agent_runner import _agent_error_message

        msg = _agent_error_message(RuntimeError("tools is not supported by this model"))
        assert "不支持函数调用" in msg, "可操作提示不能因为加了降级分支而丢失"

    def test_model_name_hint_preserved(self):
        from backend.ai.agent_runner import _agent_error_message

        msg = _agent_error_message(
            RuntimeError("model names are deepseek-flash but you passed DeepSeek-V4.1-Flash")
        )
        assert "模型 ID 覆盖" in msg

    def test_truncates_long_message(self):
        from backend.ai.agent_runner import _agent_error_message

        msg = _agent_error_message(RuntimeError("x" * 5000))
        assert len(msg) < 400, "端点报错必须截断，避免超长消息塞进 SSE"


class TestConfigSchemaPlumbing:
    """配置必须能从 API 写进去——否则兜底功能等于没接（实测踩过）"""

    def test_update_schema_has_fallback_fields(self):
        from backend.schemas.system_config import AIConfigUpdate

        fields = set(AIConfigUpdate.model_fields)
        for f in (
            "ai_fallback_enabled", "ai_fallback_model", "ai_fallback_api_key",
            "ai_fallback_base_url", "ai_fallback_model_id",
        ):
            assert f in fields, f"{f} 必须出现在 AIConfigUpdate，否则 PUT /api/system 会静默丢弃"

    def test_update_schema_accepts_magpie_payload(self):
        from backend.schemas.system_config import AIConfigUpdate

        body = AIConfigUpdate(
            ai_fallback_enabled=True,
            ai_fallback_model="openai",
            ai_fallback_api_key="sk-magpie-key-x",
            ai_fallback_base_url="http://192.168.1.25:3425/v1",
            ai_fallback_model_id="opencode-zen-free/deepseek-v4.1-flash-free",
        )
        assert body.ai_fallback_enabled is True
        assert body.ai_fallback_model_id.endswith("flash-free")
