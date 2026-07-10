"""基金详情后台刷新任务 — 进度状态管理器

把原先"在单个 HTTP 请求里串行刷新全部基金（易超时被前端掐断）"的
同步实现，改为后台任务 + 进度查询：

- POST /api/funds/refresh-details  立即返回，重活在后台 asyncio 任务中执行
- GET  /api/funds/refresh-details/status  返回实时进度（total/done/current/status）

状态保存在进程内存（单实例足够），重启后自动归零，可重新触发。
"""

import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FundRefreshState:
    """刷新任务全局状态（单例）"""

    def __init__(self) -> None:
        self.status: str = "idle"          # idle | running | done | failed
        self.total: int = 0
        self.done: int = 0
        self.current: str = ""             # 当前正在处理的基金（code name）
        self.message: str = ""             # 阶段描述
        self.error: Optional[str] = None    # 失败原因
        self.updated_at: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.results: list[dict] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "current": self.current,
            "message": self.message,
            "error": self.error,
            "updated_at": self.updated_at,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "progress": (self.done / self.total) if self.total else 0,
        }

    def reset_running(self) -> None:
        self.status = "running"
        self.total = 0
        self.done = 0
        self.current = ""
        self.message = "准备中..."
        self.error = None
        self.results = []
        self.updated_at = None
        self.started_at = datetime.now()
        self.finished_at = None


# 进程内单例（本项目单实例部署，足够使用）
_refresh_state = FundRefreshState()


def get_refresh_state() -> FundRefreshState:
    return _refresh_state


async def run_refresh_all_details() -> None:
    """后台执行：刷新全部活跃基金的阶段涨幅/扩展数据/持仓/经理，实时更新状态。

    使用独立 DB 会话（请求级会话在响应返回后已关闭，不可复用）。
    """
    from backend.database import async_session_factory
    from backend.services.fund_service import FundService
    from backend.services.fund_cache_service import (
        update_period_returns_cache,
        update_extended_detail_cache,
        get_last_refreshed_time,
    )
    from backend.services.fund_holding_service import refresh_holdings
    from backend.services.fund_manager_service import refresh_managers

    state = get_refresh_state()
    state.reset_running()
    try:
        async with async_session_factory() as db:
            svc = FundService(db)
            funds = await svc.list_funds(status="active")
            if not funds:
                state.status = "done"
                state.total = 0
                state.message = "无活跃基金"
                state.finished_at = datetime.now()
                return

            codes = [f.code for f in funds]
            name_map = {f.code: f.name for f in funds}
            state.total = len(funds)

            # 1. 阶段涨幅（批量并发）+ 扩展数据（复用 JS 文本，零额外网络）
            state.message = "刷新阶段涨幅..."
            js_texts: dict[str, str] = {}
            try:
                _, js_texts = await update_period_returns_cache(db, codes, name_map)
                logger.info("阶段涨幅缓存已更新 (%d 只)", len(codes))
            except Exception as e:
                logger.warning("阶段涨幅刷新异常: %s", e)
            if js_texts:
                try:
                    await update_extended_detail_cache(db, js_texts, name_map)
                    logger.info("扩展数据缓存已更新 (%d 只)", len(js_texts))
                except Exception as e:
                    logger.warning("扩展数据解析异常: %s", e)

            # 2. 逐只刷新持仓 + 经理（AKShare，含反爬间隔）
            for i, f in enumerate(funds):
                state.current = f"{f.code} {f.name}"
                state.message = f"刷新持仓/经理 ({i + 1}/{len(funds)})"
                try:
                    await refresh_holdings(db, f.id, f.code)
                    await refresh_managers(db, f.id, f.code)
                    state.results.append({"code": f.code, "name": f.name, "status": "ok"})
                    logger.info("刷新基金 %s 详情完成 (%d/%d)", f.code, i + 1, len(funds))
                except Exception as e:
                    logger.warning("刷新基金 %s 详情异常: %s", f.code, e)
                    state.results.append({"code": f.code, "name": f.name, "error": str(e)})
                state.done = i + 1
                if i < len(funds) - 1:
                    await asyncio.sleep(random.uniform(3, 6))

            state.updated_at = await get_last_refreshed_time(db)
            await db.commit()

        state.status = "done"
        state.message = "刷新完成"
        state.finished_at = datetime.now()
        logger.info("全部基金详情刷新完成（%d 只）", state.total)
    except Exception as e:
        logger.exception("刷新全部基金详情失败")
        state.status = "failed"
        state.error = str(e)
        state.message = "刷新失败"
        state.finished_at = datetime.now()
