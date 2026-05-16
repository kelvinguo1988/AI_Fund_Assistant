"""FastAPI 应用入口 — 生命周期管理、路由挂载"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库 + 加载调度器"""
    # ── Startup ──
    await init_db()

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
