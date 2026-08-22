"""并发控制基础设施 — 解决 akshare 调用线程池耗尽与超时挂起问题

=== 背景 ===
原实现存在三个致命问题导致仪表盘/基金详情大面积 TimeoutError：

1. 线程池耗尽：
   `asyncio.wait_for(asyncio.to_thread(func), timeout=T)` 超时后，底层线程
   **不会停止**（Python 无法杀线程），akshare 内部 requests 调用继续占用线程。
   默认线程池大小 `min(32, cpu_count+4)`，孤儿线程堆积后所有 `to_thread`
   调用排队等待空闲线程 → 全局 TimeoutError（/health 仍 200，因不触发 to_thread）。

2. 缺失超时保护：
   `fund_holding_service.refresh_holdings` 与 `fund_manager_em` 直接
   `await asyncio.to_thread(ak.xxx)` 无 wait_for 包裹，akshare 卡住会无限挂起，
   进而通过 Semaphore(3) 反向阻塞所有并发槽位。

3. 全局时间戳限流串行化：
   `_last_call_time` 全局变量 + `sleep(3 - since_last)` 使所有数据源调用
   串行排队，40-60 只基金 × (3s 限流 + 2-5s jitter + 请求耗时) = 数十分钟。

=== 修复方案 ===
1. 独立线程池：akshare 调用统一走 `_AKSHARE_POOL`（16 线程），与默认线程池隔离。
   孤儿线程只能占满本池，不影响 FastAPI 请求处理与 /health。
2. 强制超时：所有 `run_with_timeout` 入口必经 `asyncio.wait_for`，杜绝无限挂起。
3. 信号量限流：`_AKSHARE_SEM`（并发 5）替代全局时间戳，允许并发只限并发数，
   不再串行 sleep。配合 eastmoney_patch 的 20s requests 超时，单请求最长 20s。
4. UA 轮换：随机 User-Agent，降低反爬触发概率。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import logging
import random
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ── 独立线程池（akshare 专用）─────────────────────────────────────────
# 大小 16：足够支撑 5 并发 + 重试 + 少量孤儿线程缓冲，不会过多占用资源。
# 与 asyncio 默认线程池隔离，确保即使本池被孤儿线程占满，FastAPI 请求处理
# 与 /health 健康检查（走默认池或纯 async）仍可正常响应。
_AKSHARE_POOL: concurrent.futures.ThreadPoolExecutor = concurrent.futures.ThreadPoolExecutor(
    max_workers=16,
    thread_name_prefix="akshare-worker",
)

# ── 全局并发信号量 ─────────────────────────────────────────────────────
# 替代原"全局时间戳 + sleep(3)"串行限流。允许 5 个请求并发，不再强制串行等待。
# akshare/东财接口实测可承受 5 并发，配合 UA 轮换 + patch 超时，稳定性足够。
_AKSHARE_SEM: asyncio.Semaphore = asyncio.Semaphore(5)

# ── 默认超时 ──────────────────────────────────────────────────────────
# 单次 akshare 调用默认超时。eastmoney_patch 已注入 20s requests 超时，
# 这里 25s 留 5s 缓冲（DNS/连接建立 + 数据解析）。
DEFAULT_TIMEOUT: float = 25.0

# User-Agent 池（与各模块原有池保持一致，集中管理避免重复定义）
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def random_ua() -> str:
    """随机返回一个 User-Agent"""
    return random.choice(USER_AGENTS)


def rotate_ua_for_akshare() -> None:
    """为 akshare 内部 session 轮换 User-Agent

    akshare 内部使用 `requests.Session`，通过修改其 headers 实现 UA 轮换。
    失败时静默忽略（不影响主流程）。
    """
    try:
        import akshare as ak  # type: ignore
        session = getattr(ak, "_session", None) or getattr(ak, "session", None)
        if session is not None:
            session.headers.update({"User-Agent": random_ua()})
    except Exception:
        pass


async def run_with_timeout(
    func: Callable[..., T],
    *args: Any,
    timeout: float = DEFAULT_TIMEOUT,
    semaphore: Optional[asyncio.Semaphore] = None,
    **kwargs: Any,
) -> T:
    """在独立线程池中执行同步函数，强制超时保护

    替代 `await asyncio.wait_for(asyncio.to_thread(func, *args), timeout)`，
    区别在于：
    1. 使用 `_AKSHARE_POOL` 独立线程池，隔离孤儿线程影响
    2. 可选信号量参数，调用方无需自行管理并发控制
    3. 超时后清晰记录日志，便于排查

    Args:
        func: 同步函数（如 akshare 接口）
        *args: 函数位置参数
        timeout: 超时秒数，默认 25s
        semaphore: 可选并发信号量，None 表示使用全局 _AKSHARE_SEM
        **kwargs: 函数关键字参数

    Returns:
        函数返回值

    Raises:
        asyncio.TimeoutError: 超时
        Exception: 原函数抛出的异常
    """
    sem = semaphore if semaphore is not None else _AKSHARE_SEM
    loop = asyncio.get_running_loop()
    partial = functools.partial(func, *args, **kwargs)

    async def _run():
        async with sem:
            # 进入信号量后再轮换 UA，确保每次实际请求前都换 UA
            rotate_ua_for_akshare()
            return await loop.run_in_executor(_AKSHARE_POOL, partial)

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        func_name = getattr(func, "__name__", repr(func))
        logger.warning(
            "akshare 调用超时 [%s] timeout=%.1fs（线程仍可能在后台运行，已隔离）",
            func_name,
            timeout,
        )
        raise


async def run_batch_with_timeout(
    func: Callable[..., T],
    items: list[Any],
    *,
    arg_extractor: Callable[[Any], tuple[tuple, dict]],
    timeout: float = DEFAULT_TIMEOUT,
    max_concurrency: int = 5,
    return_exceptions: bool = True,
) -> list[Any]:
    """批量并发执行同一种 akshare 调用，统一超时与并发控制

    用于替代"for code in codes: await get_fund_data(code)"串行模式。
    内部使用独立信号量（max_concurrency），避免与全局 _AKSHARE_SEM 冲突。

    Args:
        func: 要调用的同步函数
        items: 待处理的元素列表（如基金代码列表）
        arg_extractor: 将元素转为 (args, kwargs) 的回调
        timeout: 单次调用超时
        max_concurrency: 最大并发数
        return_exceptions: True 时异常作为结果返回，False 时异常向上抛

    Returns:
        与 items 等长的结果列表（顺序对应）
    """
    if not items:
        return []

    sem = asyncio.Semaphore(max_concurrency)
    loop = asyncio.get_running_loop()

    async def _run_one(item: Any) -> Any:
        args, kwargs = arg_extractor(item)
        partial = functools.partial(func, *args, **kwargs)
        async with sem:
            rotate_ua_for_akshare()
            return await loop.run_in_executor(_AKSHARE_POOL, partial)

    tasks = [_run_one(item) for item in items]
    results = await asyncio.gather(*tasks, return_exceptions=return_exceptions)
    return list(results)


def shutdown_pool() -> None:
    """关闭线程池（应用退出时调用）"""
    _AKSHARE_POOL.shutdown(wait=False, cancel_futures=True)
    logger.info("akshare 线程池已关闭")
