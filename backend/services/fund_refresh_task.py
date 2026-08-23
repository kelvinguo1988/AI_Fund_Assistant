"""基金详情后台刷新任务 — 进度状态管理器

把原先"在单个 HTTP 请求里串行刷新全部基金（易超时被前端掐断）"的
同步实现，改为后台任务 + 进度查询：

- POST /api/funds/refresh-details  立即返回，重活在后台 asyncio 任务中执行
- GET  /api/funds/refresh-details/status  返回实时进度（total/done/current/status）

状态保存在进程内存（单实例足够），重启后自动归零，可重新触发。
"""

import asyncio
import logging
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

    修复说明：
    - 原实现串行 for 循环 + 每只 `sleep(3-6s)`，40-60 只 = 10-20 分钟纯等待。
    - 改为并发刷新（5 并发），每只基金使用独立 DB session（AsyncSession 非并发安全）。
    - 移除 sleep 间隔（refresh_holdings 内部已有 25s 超时 + 全局信号量限流）。
    - 进度更新使用 Lock 保护，避免并发写冲突。
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

        # 2. 并发刷新持仓 + 经理（每只独立 DB session，避免 AsyncSession 并发冲突）
        # 5 并发：与全局 akshare 信号量一致，避免过度争抢。
        state.message = "并发刷新持仓/经理..."
        sem = asyncio.Semaphore(5)
        progress_lock = asyncio.Lock()
        done_counter = [0]  # 用 list 包装以便闭包内修改

        async def _refresh_one(fund) -> dict:
            """单只基金的持仓+经理刷新（独立 session）"""
            async with sem:
                # 进度状态更新
                async with progress_lock:
                    state.current = f"{fund.code} {fund.name}"
                result = {"code": fund.code, "name": fund.name}
                last_err = None
                # SQLite 并发写（5 并发）在锁竞争剧烈时仍可能短暂报
                # "database is locked"（即使已启用 WAL + busy_timeout=30s）。
                # 对锁错误做有限重试（写操作原子，重试安全），避免持仓/经理
                # 数据因瞬时锁竞争而丢失；非锁类异常直接记录不重试。
                for _attempt in range(4):
                    try:
                        async with async_session_factory() as fund_db:
                            await refresh_holdings(fund_db, fund.id, fund.code)
                            await refresh_managers(fund_db, fund.id, fund.code)
                            await fund_db.commit()
                        result["status"] = "ok"
                        break
                    except Exception as e:  # noqa: BLE001
                        last_err = e
                        if "database is locked" in str(e).lower():
                            await asyncio.sleep(0.3 * (_attempt + 1))
                            continue
                        logger.warning("刷新基金 %s 详情异常: %s", fund.code, e)
                        break
                else:
                    logger.warning("刷新基金 %s 详情异常(锁重试耗尽): %s", fund.code, last_err)
                if last_err is not None and result.get("status") != "ok":
                    result["error"] = str(last_err)

                async with progress_lock:
                    done_counter[0] += 1
                    state.done = done_counter[0]
                    state.results.append(result)
                return result

        # 并发执行所有基金刷新
        await asyncio.gather(
            *[_refresh_one(f) for f in funds], return_exceptions=True
        )

        # 获取最终刷新时间
        async with async_session_factory() as db:
            state.updated_at = await get_last_refreshed_time(db)

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
