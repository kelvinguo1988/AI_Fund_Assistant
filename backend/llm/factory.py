"""LLM 工厂 — 根据配置创建 Provider"""

from backend.llm.base import BaseLLMProvider


class LLMFactory:
    """LLM 工厂"""

    @staticmethod
    def create(model_name: str, api_key: str, base_url: str) -> BaseLLMProvider:
        """根据模型名称创建 Provider

        Args:
            model_name: 模型名称（deepseek / openai / tongyi）
            api_key: API Key
            base_url: API Base URL

        Returns:
            BaseLLMProvider 实例
        """
        if model_name == "deepseek":
            from backend.llm.deepseek_provider import DeepSeekProvider
            return DeepSeekProvider(model_name="deepseek-chat", api_key=api_key, base_url=base_url)
        elif model_name == "openai":
            from backend.llm.openai_provider import OpenAIProvider
            return OpenAIProvider(model_name="gpt-4o-mini", api_key=api_key, base_url=base_url)
        elif model_name == "tongyi":
            from backend.llm.tongyi_provider import TongyiProvider
            return TongyiProvider(model_name="qwen-plus", api_key=api_key, base_url=base_url)
        else:
            # 默认使用 DeepSeek 兼容接口
            from backend.llm.deepseek_provider import DeepSeekProvider
            return DeepSeekProvider(model_name=model_name, api_key=api_key, base_url=base_url)
