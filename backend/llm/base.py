"""LLM 抽象基类"""

from abc import ABC, abstractmethod
from typing import Optional


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
        """发送对话请求

        Args:
            system_prompt: 系统提示词
            messages: 对话历史 [{"role": "user"/"assistant", "content": "..."}]
            max_tokens: 最大生成 token 数
            temperature: 生成温度

        Returns:
            AI 回复文本
        """
        ...
