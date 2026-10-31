"""LLM 凭证回写（2026-10-31）

背景：Trae / Qoder 的 refresh token **每次刷新都会轮换，旧值立刻失效**。
provider 若只在内存里更新，进程一重启（部署、崩溃恢复）就用回已作废的旧值，
表现为「过一段时间就莫名其妙认证失败，得手动重新填凭证」。

这里把轮换后的凭证写回 `system_config.ai_api_key`，让它跨重启存活。

三条安全约束（都是踩过坑才加的）：
1. **不抛异常**：回写失败绝不能让聊天请求失败——凭证已在内存里生效，
   写库只是为了让下次启动也能用。
2. **只在本 provider 仍是当前配置时才写**：用户在刷新途中切到别的 provider
   （如 zen-free）时，写入会把新配置覆盖掉。因此校验 `ai_model` 与 realm 一致。
3. **内容相同就不写**：避免无意义的库写入与 updated_at 抖动。
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 允许回写的 provider → 它的 realm 标识（与 system_config.ai_model 取值一致）
_WRITABLE = {
    "trae-cn", "trae-global", "qoder", "qoder-cn", "workbuddy", "workbuddy-ai",
}


def _merge(existing: str, updates: dict) -> Optional[str]:
    """把 updates 合并进已有凭证 JSON；无变化返回 None"""
    if not updates:
        return None
    try:
        base = json.loads(existing) if (existing or "").strip().startswith("{") else {}
    except json.JSONDecodeError:
        base = {}
    if not isinstance(base, dict):
        base = {}
    changed = False
    for k, v in updates.items():
        if v and base.get(k) != v:
            base[k] = v
            changed = True
    if not changed:
        return None
    return json.dumps(base, ensure_ascii=False)


async def persist_credential(realm: str, changes: dict) -> bool:
    """把轮换后的凭证字段写回 system_config.ai_api_key

    Args:
        realm: provider 标识（trae-cn / qoder-cn / …）
        changes: 要更新的字段，如 {"refresh": "新值"}

    Returns:
        True=已写入；False=无需写或未写入（都不影响调用方）
    """
    if realm not in _WRITABLE:
        return False
    if not changes or not any(changes.values()):
        return False
    try:
        from sqlalchemy import select

        from backend.database import async_session_factory
        from backend.models.system_config import SystemConfig

        async with async_session_factory() as db:
            rows = (await db.execute(select(SystemConfig))).scalars().all()
            cm = {c.config_key: c.config_value for c in rows}

            # 约束 2：本 provider 必须仍是当前配置，否则会覆盖用户的新选择
            current_model = (cm.get("ai_model") or "").strip()
            if current_model != realm:
                logger.info(
                    f"凭证回写跳过：当前 ai_model={current_model or '(空)'}，"
                    f"本次刷新来自 {realm}"
                )
                return False

            merged = _merge(cm.get("ai_api_key") or "", changes)
            if merged is None:
                return False  # 约束 3：内容没变

            row = next((c for c in rows if c.config_key == "ai_api_key"), None)
            if row is None:
                db.add(SystemConfig(config_key="ai_api_key", config_value=merged))
            else:
                row.config_value = merged
            await db.commit()
            logger.info(
                f"{realm} 凭证已回写（更新字段: {', '.join(k for k, v in changes.items() if v)}）"
            )
            return True
    except Exception as e:  # noqa: BLE001 - 约束 1：绝不向上抛
        logger.warning(f"凭证回写失败（不影响本次调用）: {type(e).__name__}: {e}")
        return False
