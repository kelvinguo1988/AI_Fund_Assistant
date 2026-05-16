"""DeepSeek Provider — 使用 OpenAI SDK 兼容接口"""

import logging
from typing import Optional

from openai import AsyncOpenAI

from backend.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek LLM Provider

    使用 OpenAI SDK 的 chat.completions.create 接口，
    DeepSeek API 完全兼容 OpenAI API 格式。
    """

    def __init__(self, model_name: str = "deepseek-chat", api_key: str = "", base_url: str = "https://api.deepseek.com/v1") -> None:
        super().__init__(model_name, api_key, base_url)
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        """发送对话请求"""
        try:
            all_messages = [{"role": "system", "content": system_prompt}] + messages
            response = await self._client.chat.completions.create(
                model=self.model_name,
                messages=all_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = response.choices[0].message.content
            return content or ""
        except Exception as e:
            logger.error(f"DeepSeek API 调用失败: {e}")
            raise
