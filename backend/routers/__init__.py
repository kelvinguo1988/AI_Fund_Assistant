"""路由汇总"""

from fastapi import APIRouter

from backend.routers.fund import router as fund_router
from backend.routers.factor import router as factor_router
from backend.routers.push_channel import router as push_channel_router
from backend.routers.schedule import router as schedule_router
from backend.routers.report_config import router as report_config_router
from backend.routers.analysis import router as analysis_router
from backend.routers.system_config import router as system_config_router
from backend.routers.ai_chat import router as ai_chat_router

router = APIRouter()

router.include_router(fund_router, prefix="/funds", tags=["基金池"])
router.include_router(factor_router, prefix="/factors", tags=["因子管理"])
router.include_router(push_channel_router, prefix="/push-channels", tags=["推送渠道"])
router.include_router(schedule_router, prefix="/schedules", tags=["调度计划"])
router.include_router(report_config_router, prefix="/report-config", tags=["报告配置"])
router.include_router(analysis_router, prefix="/analysis", tags=["分析结果"])
router.include_router(ai_chat_router, prefix="/ai", tags=["AI 对话"])
router.include_router(system_config_router, prefix="/system", tags=["系统配置"])
