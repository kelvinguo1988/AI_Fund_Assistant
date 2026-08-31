"""FastAPI 应用入口 — 生命周期管理、路由挂载"""

import asyncio
import logging
import logging.handlers
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from backend.config import settings


def _setup_logging() -> None:
    """统一日志配置：控制台 + 文件（TimedRotatingFileHandler，保留 7 天）

    - 文件日志每天午夜轮转，自动删除 7 天前的旧文件（增量覆盖语义）；
    - uvicorn/access/access_error 三个 logger 一并接管，请求日志同样落盘；
    - 文件不可写（如只读容器）时降级为仅控制台，不阻塞启动。
    """
    level = logging.DEBUG if settings.DEBUG else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(level)
    # 清掉可能已存在的 handler（uvicorn --reload 重载时防重复输出）
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        log_dir = Path(settings.DATABASE_DIR) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_dir / "app.log",
            when="midnight",       # 每天午夜切割
            backupCount=7,         # 保留 7 天：新日志写入 app.log，
                                   # 7 天前的旧文件在轮转时被自动删除
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        root.warning(f"日志文件初始化失败，仅使用控制台输出: {e}")

    # 接管 uvicorn 的三个 logger，统一走 root 的 handler 与格式
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True


logger = logging.getLogger(__name__)

# 模块导入即配置（须早于其他业务模块的首次 getLogger 使用）
_setup_logging()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.config import settings
from backend.database import init_db


async def _prewarm_market_cache():
    """后台预热市场数据缓存，避免用户首次请求等待"""
    try:
        from backend.services.market_service import MarketService
        svc = MarketService()
        import asyncio
        # 并行预热所有数据源
        await asyncio.gather(
            svc.get_market_capital_flow(),
            svc.get_sector_flow_rankings(),
            svc.get_hsgt_flow(),
            svc.get_market_adv_decline(),
            svc.get_market_turnover(),
            return_exceptions=True,
        )
        logger.info("市场数据缓存预热完成")

        # 市场环境快照预热（估值分位 xls ~2MB + 两融接口，避免首次分析串行等待）
        try:
            from backend.services.market_regime_service import MarketRegimeService
            await MarketRegimeService().get_snapshot()
            logger.info("市场环境快照预热完成")
        except Exception as e:
            logger.warning(f"市场环境快照预热失败: {e}")
    except Exception as e:
        logger.warning(f"市场数据缓存预热失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库 + 加载调度器 + 应用反爬补丁"""
    # ── Startup ──
    # 应用东方财富反爬虫补丁
    try:
        from backend.patch.eastmoney_patch import apply_patch
        apply_patch()
    except Exception as e:
        logger.warning(f"EastMoney 反爬虫补丁加载失败: {e}")

    await init_db()

    # 后台预热市场数据缓存（不阻塞启动）
    asyncio.ensure_future(_prewarm_market_cache())

    # 启动调度器
    from backend.scheduler.task_scheduler import task_scheduler
    task_scheduler.start()

    yield

    # ── Shutdown ──
    task_scheduler.shutdown()

    # 释放数据库连接池
    try:
        from backend.database import engine
        await engine.dispose()
    except Exception as e:
        logger.warning(f"关闭数据库引擎失败: {e}")

    # 关闭 akshare 独立线程池，避免孤儿线程残留
    try:
        from backend.utils.concurrency import shutdown_pool
        shutdown_pool()
    except Exception as e:
        logger.warning(f"关闭线程池失败: {e}")


app = FastAPI(
    title="基金量化交易系统",
    description="基金量化分析 + AI 对话 + 定时推送",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS 中间件 ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 挂载路由 ─────────────────────────────────────────────────────────
from backend.routers import (  # noqa: E402
    router as api_router,
)

app.include_router(api_router, prefix="/api")


# ── 健康检查 ─────────────────────────────────────────────────────────
@app.get("/health", tags=["系统"])
async def health_check():
    return {"status": "ok"}


# ── 前端静态文件服务 ─────────────────────────────────────────────────
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _frontend_available() -> bool:
    return FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file()


@app.get("/", tags=["前端"])
async def serve_frontend_index():
    """提供前端首页（SPA 入口）"""
    if not _frontend_available():
        raise HTTPException(
            status_code=404,
            detail="Frontend not built. Run: cd frontend && npm install && npm run build",
        )
    return FileResponse(FRONTEND_DIST / "index.html", media_type="text/html")


@app.get("/{full_path:path}", tags=["前端"])
async def serve_frontend(full_path: str):
    """提供前端静态资源 + SPA 路由回退"""
    # 不影响 API 路由（FastAPI 优先匹配精确路由）
    file_path = FRONTEND_DIST / full_path
    if file_path.is_file():
        return FileResponse(file_path)

    # SPA 回退：未匹配的前端路径统一返回 index.html
    if _frontend_available():
        return FileResponse(FRONTEND_DIST / "index.html", media_type="text/html")

    raise HTTPException(status_code=404, detail="Not Found")
