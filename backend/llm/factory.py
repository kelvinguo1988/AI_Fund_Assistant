"""LLM 工厂 — 根据配置创建 Provider"""

from typing import Optional

from backend.llm.base import BaseLLMProvider


class LLMFactory:
    """LLM 工厂"""

    # preset key -> 默认模型 ID
    _PRESET_MODELS = {
        "deepseek": "deepseek-flash",
        "openai": "gpt-4o-mini",
        "tongyi": "qwen-plus",
        "glm": "glm-4-flash",
        "zen-free": "deepseek-v4.1-flash-free",
        "workbuddy": "deepseek-v4.1-flash",
        "workbuddy-ai": "deepseek-v4.1-flash",
    }

    @staticmethod
    def create(
        model_name: str, api_key: str, base_url: str,
        model_id: Optional[str] = None,
    ) -> BaseLLMProvider:
        """根据模型名称创建 Provider

        Args:
            model_name: 预设名（deepseek / glm / tongyi / openai）或自定义
            api_key: API Key
            base_url: API Base URL
            model_id: 具体模型 ID 覆盖（如 glm-4-plus / qwen-max / deepseek-reasoner）；
                      为空时用预设默认值

        Returns:
            BaseLLMProvider 实例
        """
        resolved = (model_id or "").strip() or LLMFactory._PRESET_MODELS.get(model_name, model_name)
        if model_name == "glm":
            from backend.llm.glm_provider import GLMProvider
            return GLMProvider(model_name=resolved, api_key=api_key, base_url=base_url)
        elif model_name == "zen-free":
            # OpenCode Zen 免费模型（2026-10-20）：自动补齐免费层要求的
            # 端点/UA/核心工具/流式特征，不需要本地任何软件
            from backend.llm.zen_free_provider import ZenFreeProvider
            return ZenFreeProvider(model_name=resolved, api_key=api_key, base_url=base_url)
        elif model_name in ("workbuddy", "workbuddy-ai"):
            # WorkBuddy（2026-10-20）：用用户自己账号的 OAuth token，
            # api_key 位置填 refresh token，access 由 Provider 自动换取
            from backend.llm.workbuddy_provider import WorkBuddyProvider
            return WorkBuddyProvider(
                model_name=resolved, api_key=api_key, base_url=base_url,
                site_id=model_name,
            )
        elif model_name == "tongyi":
            from backend.llm.tongyi_provider import TongyiProvider
            return TongyiProvider(model_name=resolved, api_key=api_key, base_url=base_url)
        elif model_name == "openai":
            from backend.llm.openai_provider import OpenAIProvider
            return OpenAIProvider(model_name=resolved, api_key=api_key, base_url=base_url)
        else:
            # deepseek 及其他 OpenAI 兼容接口
            from backend.llm.deepseek_provider import DeepSeekProvider
            return DeepSeekProvider(model_name=resolved, api_key=api_key, base_url=base_url)
