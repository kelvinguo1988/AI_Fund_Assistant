"""FastAPI 应用入口 — 生命周期管理、路由挂载"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

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
