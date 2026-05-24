"""基金变更检测 — 计算持仓和经理变更信息（不修改 Fund.tags）

所有变更信息在请求时实时计算，不存储到基金标签中。
变更标签仅在前端 基金详情 页面展示。
"""

import logging
import re
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.fund_holding_service import compute_holding_changes
from backend.services.fund_manager_service import compute_manager_changes

logger = logging.getLogger(__name__)

_QUARTER_PATTERN = re.compile(r"(\d{4})年(\d)季度")


def _parse_quarter(label: str) -> Optional[tuple[int, int]]:
    """从"2025年3季度股票投资明细"解析出 (2025, 3)"""
    m = _QUARTER_PATTERN.search(label)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


async def get_fund_changes(db: AsyncSession, fund_id: int) -> dict:
    """获取基金完整变更信息（用于前端展示）

    Args:
        db: 数据库会话
        fund_id: 基金 ID

    Returns:
        {
            "holding_changes": { ... } | None,
            "manager_changes": { ... } | None,
            "tags": ["2026Q1调仓", "经理变更"],  # 仅展示用标签
        }
    """
    holding = await compute_holding_changes(db, fund_id)
    manager = await compute_manager_changes(db, fund_id)

    tags: list[str] = []
    if holding and (holding["added"] or holding["removed"]):
        parsed = _parse_quarter(holding["latest_quarter"])
        if parsed:
            tags.append(f"{parsed[0]}Q{parsed[1]}调仓")
    if manager and manager["changed"]:
        tags.append("经理变更")

    return {
        "holding_changes": holding,
        "manager_changes": manager,
        "tags": tags,
    }
