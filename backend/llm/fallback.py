"""LLM 兜底链路（2026-10-20）

背景：主力模型的 Key/额度失效时，AI 工作台与普通对话会直接报「AI 服务调用失败」，
用户只能自己进系统设置换 Key。常见场景是付费额度用完、或出口被封禁。

方案：可选的备用 Provider。主 Provider 调用抛错时，**同一个请求**自动重试一次备用
Provider；仍失败才向上抛错。默认关闭（备用 Key 为空即不启用），不改变现有行为。

配置项（system_config，均为可选项）:
  ai_fallback_enabled   是否启用（"true"/"false"，缺省 false）
  ai_fallback_model     预设名（deepseek/openai/glm/tongyi），缺省 openai
  ai_fallback_api_key   备用 Key，空 = 不启用
  ai_fallback_base_url  备用 Base URL，缺省沿用主 Base URL
  ai_fallback_model_id  备用模型 ID（如 opencode-zen-free/deepseek-v4.1-flash-free）

安全：备用 Key 与主 Key 同等对待——只进日志的错误摘要做脱敏，不外泄到接口。
"""

import logging
from typing import Optional

from backend.llm.base import BaseLLMProvider
from backend.llm.factory import LLMFactory

logger = logging.getLogger(__name__)


def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def load_fallback_config(config_map: dict) -> Optional[dict]:
    """从 system_config 映射里读备用 Provider 配置；未启用返回 None

    显式开关（ai_fallback_enabled）与「备用 Key 非空」两个条件都满足才算启用，
    避免只填了 URL 没填 Key 时每轮都白试一次。
    """
    if not _truthy(config_map.get("ai_fallback_enabled")):
        return None
    api_key = (config_map.get("ai_fallback_api_key") or "").strip()
    if not api_key:
        return None
    return {
        "model": (config_map.get("ai_fallback_model") or "openai").strip() or "openai",
        "api_key": api_key,
        "base_url": (config_map.get("ai_fallback_base_url") or "").strip()
        or (config_map.get("ai_base_url") or "").strip(),
        "model_id": (config_map.get("ai_fallback_model_id") or "").strip() or None,
    }


def create_provider(
    model: str, api_key: str, base_url: str, model_id: Optional[str] = None
) -> BaseLLMProvider:
    """创建主 Provider（薄封装，便于测试替换）"""
    return LLMFactory.create(model, api_key, base_url, model_id=model_id)


def create_fallback_provider(cfg: dict) -> Optional[BaseLLMProvider]:
    """创建备用 Provider；配置不完整或构造失败返回 None（不阻断主链路）"""
    if not cfg or not cfg.get("api_key"):
        return None
    try:
        return create_provider(
            cfg["model"], cfg["api_key"], cfg["base_url"], model_id=cfg.get("model_id")
        )
    except Exception as e:  # noqa: BLE001 - 备用链路构造失败不得影响主链路
        logger.warning(f"备用 LLM Provider 构造失败: {type(e).__name__}: {e}")
        return None


def describe_fallback(cfg: Optional[dict]) -> str:
    """日志用的备用链路描述（不含密钥）"""
    if not cfg:
        return "未启用"
    return f"{cfg.get('model')}/{cfg.get('model_id') or '-'} @ {cfg.get('base_url')}"
