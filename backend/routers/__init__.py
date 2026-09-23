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
from backend.routers.ai_agent import router as ai_agent_router
from backend.routers.ai_skill import router as ai_skill_router
from backend.routers.backtest import router as backtest_router
from backend.routers.holiday import router as holiday_router
from backend.routers.position import router as position_router

router = APIRouter()

router.include_router(fund_router, prefix="/funds", tags=["基金池"])
router.include_router(factor_router, prefix="/factors", tags=["因子管理"])
router.include_router(push_channel_router, prefix="/push-channels", tags=["推送渠道"])
router.include_router(schedule_router, prefix="/schedules", tags=["调度计划"])
router.include_router(report_config_router, prefix="/report-config", tags=["报告配置"])
router.include_router(analysis_router, prefix="/analysis", tags=["分析结果"])
router.include_router(ai_chat_router, prefix="/ai", tags=["AI 对话"])
router.include_router(ai_agent_router, prefix="/ai/agent", tags=["AI 分析 Agent"])
# 注意：ai_skill 路由自带 /skills 路径前缀，此处只挂 /ai（曾误挂 /ai/skills 致前端 404）
router.include_router(ai_skill_router, prefix="/ai", tags=["AI Skills"])
router.include_router(system_config_router, prefix="/system", tags=["系统配置"])
router.include_router(backtest_router, prefix="/backtest", tags=["信号回测"])
router.include_router(holiday_router, prefix="/holiday", tags=["调休同步"])
router.include_router(position_router, prefix="/positions", tags=["我的持仓"])
