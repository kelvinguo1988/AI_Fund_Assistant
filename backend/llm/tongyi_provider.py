"""通义千问 Provider — 使用 OpenAI SDK 兼容接口"""

import logging

from openai import AsyncOpenAI

from backend.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class TongyiProvider(BaseLLMProvider):
    """通义千问 LLM Provider

    通义千问兼容 OpenAI API 格式，
    Base URL: https://dashscope.aliyuncs.com/compatible-mode/v1
    """

    def __init__(self, model_name: str = "qwen-plus", api_key: str = "", base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1") -> None:
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
            logger.error(f"通义千问 API 调用失败: {e}")
            raise
